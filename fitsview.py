#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fitsview — visionneuse pour trier ses brutes d'astrophotographie.

- Glisser-déposer de fichiers FITS, de vidéos SER, de RAW Canon (CR2)
  ou de dossiers
- Liste des images ; une vidéo SER y apparaît image par image
- Auto-ajustement de l'histogramme (type STF de PixInsight)
- Débayerisation rapide (super-pixel) si la matrice de Bayer est connue
- Lecture de la liste comme une vidéo (SER ou série de FITS)
- Marquage des images à rejeter (Espace, Suppr ou X)
- Rangement des fichiers rejetés dans « rejetes » (ou suppression)
- Export des images choisies en vidéo SER ou en fichiers FITS

Dépendances : python3, PyQt5, numpy, astropy
Pour les CR2 : le module Python rawpy, ou à défaut le programme dcraw
"""

import os
import sys
import shutil
import struct
import subprocess
import functools
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import numpy as np
from astropy.io import fits

from PyQt5.QtCore import (Qt, QObject, QRunnable, QThread, QThreadPool, QTimer,
                          pyqtSignal)
from PyQt5.QtGui import (QImage, QPixmap, QColor, QFont, QPainter, QKeySequence,
                         QIcon)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QListWidget, QListWidgetItem, QSplitter,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QFileDialog,
    QMessageBox, QPlainTextEdit, QWidget, QVBoxLayout, QLabel, QComboBox,
    QCheckBox, QAction, QAbstractItemView, QProgressDialog, QSpinBox,
)

APP_NAME = "fitsview"
FITS_EXT = (".fit", ".fits", ".fts", ".fit.gz", ".fits.gz")
SER_EXT = (".ser",)
RAW_EXT = (".cr2",)
REJECT_DIR = "rejetes"
CACHE_MAX = 12

# Chaque image de la liste est désignée par une « source » :
#   (chemin, -1) pour un fichier FITS, (chemin, n) pour l'image n d'un SER
ROLE_SRC = Qt.UserRole
ROLE_REJ = Qt.UserRole + 1

# nom affiché -> (type, paramètre a, paramètre b)
#   stf : a = écrêtage des ombres (en MAD), b = fond de ciel cible
#   pct : a, b = percentiles bas / haut
STRETCH_MODES = OrderedDict([
    ("Auto-stretch (STF)", ("stf", -2.8, 0.25)),
    ("Auto-stretch doux", ("stf", -3.5, 0.15)),
    ("Auto-stretch fort", ("stf", -2.0, 0.40)),
    ("Percentiles 0,5 – 99,8 %", ("pct", 0.5, 99.8)),
    ("Linéaire min – max", ("lin", None, None)),
])

BAYER_PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")


class Cancelled(Exception):
    pass


def is_fits(path):
    return path.lower().endswith(FITS_EXT)


def is_ser(path):
    return path.lower().endswith(SER_EXT)


def is_raw(path):
    return path.lower().endswith(RAW_EXT)


def is_supported(path):
    return is_fits(path) or is_ser(path) or is_raw(path)


def src_name(src):
    path, frame = src
    name = os.path.basename(path)
    return name if frame < 0 else f"{name}  #{frame + 1}"


def to_ascii(text, n=68):
    return str(text).encode("ascii", "replace").decode("ascii")[:n]


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(f"{base}_{i}{ext}"):
        i += 1
    return f"{base}_{i}{ext}"


# ----------------------------------------------------------------------------
# Lecture FITS
# ----------------------------------------------------------------------------

def read_fits(path):
    """Renvoie (données float32 2D ou HxWx3, header, BITPIX d'origine)
    du premier HDU contenant une image."""
    with fits.open(path, memmap=False) as hdul:
        for hdu in hdul:
            if hdu.header.get("NAXIS", 0) < 2:
                continue
            header = hdu.header.copy()          # avant mise à l'échelle
            bitpix = int(header.get("BITPIX", 16))
            raw = hdu.data
            if raw is None or raw.ndim < 2:
                continue
            data = np.asarray(raw, dtype=np.float32)
            break
        else:
            raise ValueError("aucune image trouvée dans ce fichier")

    if data.ndim == 3:
        if data.shape[0] in (3, 4):          # cube (3, H, W)
            data = np.moveaxis(data[:3], 0, -1)
        elif data.shape[-1] in (3, 4):       # (H, W, 3)
            data = data[..., :3]
        else:                                # cube quelconque : 1er plan
            data = data[0]
    elif data.ndim > 3:
        data = data.reshape(-1, *data.shape[-2:])[0]

    data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
    return np.ascontiguousarray(data), header, bitpix


# ----------------------------------------------------------------------------
# Lecture SER
# ----------------------------------------------------------------------------

SER_HEADER = struct.Struct("<14s7i40s40s40s2q")      # 178 octets
SER_BAYER = {8: "RGGB", 9: "GRBG", 10: "GBRG", 11: "BGGR"}
SER_COLOR_IDS = {"MONO": 0, "RGGB": 8, "GRBG": 9, "GBRG": 10, "BGGR": 11,
                 "RGB": 100}
TICKS_EPOCH = datetime(1, 1, 1)


def _ser_text(b):
    return b.split(b"\0", 1)[0].decode("latin-1", "replace").strip()


def ticks_to_datetime(t):
    return TICKS_EPOCH + timedelta(microseconds=int(t) // 10)


def to_ticks(dt):
    """Format de date SER : intervalles de 100 ns depuis le 01/01/0001."""
    td = dt - TICKS_EPOCH
    return (td.days * 86400 + td.seconds) * 10_000_000 + td.microseconds * 10


class SerFile:
    """Accès en lecture aux images d'une vidéo SER (sans tout charger)."""

    def __init__(self, path):
        self.path = path
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            raw = f.read(SER_HEADER.size)
        if len(raw) < SER_HEADER.size:
            raise ValueError("fichier SER trop court")
        (fid, _lu, color_id, big_endian, w, h, depth, count,
         observer, instrument, telescope, _local, utc) = SER_HEADER.unpack(raw)
        if not fid.startswith(b"LUCAM-RECORDER"):
            raise ValueError("ce n'est pas un fichier SER")
        if w <= 0 or h <= 0 or depth <= 0:
            raise ValueError("en-tête SER invalide")

        self.width, self.height, self.depth = w, h, depth
        self.color_id = color_id
        self.planes = 3 if color_id in (100, 101) else 1
        self.bpp = 1 if depth <= 8 else 2
        # Le drapeau « LittleEndian » est utilisé à l'envers par presque tous
        # les logiciels (Siril, PIPP…) : 0 = petit-boutiste, 1 = gros-boutiste
        self.dtype = np.dtype(np.uint8 if self.bpp == 1
                              else (">u2" if big_endian else "<u2"))
        self.frame_size = w * h * self.planes * self.bpp
        available = max(0, (size - SER_HEADER.size) // self.frame_size)
        self.count = min(count, available) if count > 0 else available
        if self.count == 0:
            raise ValueError("aucune image dans ce fichier SER")

        self.timestamps = None
        trailer = SER_HEADER.size + count * self.frame_size
        if count == self.count and size >= trailer + 8 * count:
            ts = np.fromfile(path, dtype="<i8", count=count, offset=trailer)
            if (ts > 0).all():
                self.timestamps = ts
        self.utc = utc
        self.observer = _ser_text(observer)
        self.instrument = _ser_text(instrument)
        self.telescope = _ser_text(telescope)

    def read_frame(self, i):
        n = self.width * self.height * self.planes
        a = np.fromfile(self.path, dtype=self.dtype, count=n,
                        offset=SER_HEADER.size + i * self.frame_size)
        if a.size != n:
            raise ValueError(f"image {i + 1} incomplète")
        if self.planes == 3:
            a = a.reshape(self.height, self.width, 3)
            if self.color_id == 101:                 # BGR -> RGB
                a = a[..., ::-1]
        else:
            a = a.reshape(self.height, self.width)
        return a

    def header(self, i):
        h = fits.Header()
        h["ROWORDER"] = ("TOP-DOWN", "Order of the rows in the image")
        bayer = SER_BAYER.get(self.color_id)
        if bayer:
            h["BAYERPAT"] = (bayer, "Bayer color pattern")
        if self.timestamps is not None:
            dt = ticks_to_datetime(self.timestamps[i])
            h["DATE-OBS"] = (dt.isoformat(timespec="microseconds"),
                             "UTC, from SER trailer")
        for key, val in (("INSTRUME", self.instrument),
                         ("TELESCOP", self.telescope),
                         ("OBSERVER", self.observer)):
            if val:
                h[key] = to_ascii(val)
        h["SERFILE"] = (to_ascii(os.path.basename(self.path)), "Source SER")
        h["SERFRAME"] = (i + 1, "Frame number in the SER file")
        h["SERCOUNT"] = (self.count, "Number of frames in the SER file")
        h["SERDEPTH"] = (self.depth, "Bits per pixel in the SER file")
        return h


@functools.lru_cache(maxsize=16)
def open_ser(path):
    return SerFile(path)


# ----------------------------------------------------------------------------
# Lecture RAW (Canon CR2)
# ----------------------------------------------------------------------------

def read_tiff_exif(path, limit=2 * 1024 * 1024):
    """Lit quelques champs EXIF d'un fichier RAW au format TIFF (CR2…).
    Renvoie un dict, éventuellement vide ; ne lève pas d'exception."""
    out = {}
    try:
        with open(path, "rb") as f:
            buf = f.read(limit)
        e = {b"II": "<", b"MM": ">"}.get(buf[:2])
        if e is None or struct.unpack_from(e + "H", buf, 2)[0] != 42:
            return out
        sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}

        def entries(off):
            n = struct.unpack_from(e + "H", buf, off)[0]
            res = {}
            for k in range(min(n, 500)):
                tag, typ, cnt, raw = struct.unpack_from(e + "HHI4s", buf,
                                                        off + 2 + 12 * k)
                res[tag] = (typ, cnt, raw)
            return res

        def value(entry):
            typ, cnt, raw = entry
            size = sizes.get(typ, 1) * cnt
            if size > 4:
                pos = struct.unpack(e + "I", raw)[0]
                raw = buf[pos:pos + size]
                if len(raw) < size:
                    return None
            if typ == 2:
                return raw[:cnt].split(b"\0", 1)[0].decode("latin-1").strip()
            if typ == 3:
                return struct.unpack_from(e + "H", raw)[0]
            if typ == 4:
                return struct.unpack_from(e + "I", raw)[0]
            if typ in (5, 10):
                num, den = struct.unpack_from(e + ("II" if typ == 5 else "ii"),
                                              raw)
                return num / den if den else None
            return None

        ifd0 = entries(struct.unpack_from(e + "I", buf, 4)[0])
        for tag, key in ((0x010F, "make"), (0x0110, "model"),
                         (0x0132, "datetime")):
            if tag in ifd0:
                out[key] = value(ifd0[tag])
        if 0x8769 in ifd0:
            exif = entries(value(ifd0[0x8769]))
            for tag, key in ((0x829A, "exptime"), (0x8827, "iso"),
                             (0x9003, "datetime"), (0x920A, "focal")):
                if tag in exif:
                    v = value(exif[tag])
                    if v is not None:
                        out[key] = v
    except (OSError, struct.error, ValueError, TypeError):
        pass
    return out


def parse_pnm(buf):
    """Décode une image PGM/PPM binaire (sortie de dcraw)."""
    if buf[:2] not in (b"P5", b"P6"):
        raise ValueError("sortie de dcraw inattendue")
    vals, i = [], 2
    while len(vals) < 3:
        while buf[i:i + 1].isspace():
            i += 1
        if buf[i:i + 1] == b"#":
            i = buf.index(b"\n", i) + 1
            continue
        j = i
        while not buf[j:j + 1].isspace():
            j += 1
        vals.append(int(buf[i:j]))
        i = j
    i += 1
    w, h, maxval = vals
    planes = 3 if buf[:2] == b"P6" else 1
    dtype = np.dtype(">u2") if maxval > 255 else np.dtype(np.uint8)
    a = np.frombuffer(buf, dtype=dtype, count=w * h * planes, offset=i)
    a = a.reshape((h, w, 3) if planes == 3 else (h, w))
    return a.astype(np.uint16)


def decode_raw(path):
    """Données brutes du capteur (uint16, sans dématriçage) et matrice
    de Bayer de la zone visible. Utilise rawpy, sinon dcraw."""
    try:
        import rawpy
    except ImportError:
        rawpy = None

    if rawpy is not None:
        with rawpy.imread(path) as raw:
            data = np.array(raw.raw_image_visible, dtype=np.uint16)
            pattern = None
            rp = np.asarray(raw.raw_pattern)
            if rp.shape == (2, 2):
                desc = raw.color_desc.decode("ascii", "replace")
                p = "".join(desc[i] for i in rp.flatten())
                pattern = p if p in BAYER_PATTERNS else None
        return data, pattern

    exe = shutil.which("dcraw")
    if exe is None:
        raise ValueError("pour lire les fichiers CR2, installez le programme "
                         "dcraw (par ex. sudo apt install dcraw) ou le module "
                         "Python rawpy")
    env = dict(os.environ, LC_ALL="C")
    run = subprocess.run([exe, "-D", "-4", "-t", "0", "-c", path],
                         capture_output=True, env=env, timeout=180)
    if run.returncode != 0 or not run.stdout:
        msg = run.stderr.decode("utf-8", "replace").strip()
        raise ValueError(msg or "dcraw n'a pas pu lire ce fichier")
    data = parse_pnm(run.stdout)
    if data.ndim != 2:
        raise ValueError("ce RAW n'a pas de matrice de Bayer")

    pattern = None
    info = subprocess.run([exe, "-i", "-v", path], capture_output=True,
                          env=env, timeout=60)
    for line in info.stdout.decode("latin-1").splitlines():
        if line.startswith("Filter pattern:"):
            p = line.split(":", 1)[1].strip().replace("/", "")[:4].upper()
            pattern = p if p in BAYER_PATTERNS else None
    return data, pattern


def read_raw(path):
    data, pattern = decode_raw(path)
    meta = read_tiff_exif(path)

    h = fits.Header()
    h["ROWORDER"] = ("TOP-DOWN", "Order of the rows in the image")
    if pattern:
        h["BAYERPAT"] = (pattern, "Bayer color pattern")
    if isinstance(meta.get("exptime"), float):
        h["EXPTIME"] = (round(meta["exptime"], 6), "[s] Exposure time (EXIF)")
    if meta.get("iso"):
        h["ISOSPEED"] = (int(meta["iso"]), "ISO speed (EXIF)")
    if isinstance(meta.get("focal"), float) and meta["focal"] > 0:
        h["FOCALLEN"] = (round(meta["focal"], 1), "[mm] Focal length (EXIF)")
    model = meta.get("model") or ""
    make = meta.get("make") or ""
    camera = model if model.startswith(make) else f"{make} {model}".strip()
    if camera:
        h["INSTRUME"] = to_ascii(camera)
    try:
        dt = datetime.strptime(meta.get("datetime", ""), "%Y:%m:%d %H:%M:%S")
        h["DATE-OBS"] = (dt.isoformat(), "Camera clock (EXIF), time zone unknown")
    except ValueError:
        pass
    h["RAWFILE"] = (to_ascii(os.path.basename(path)), "Source RAW file")
    return data.astype(np.float32), h, 16


def read_source(src):
    """(données float32, header, BITPIX) pour un FITS ou une image de SER."""
    path, frame = src
    if frame < 0:
        return read_raw(path) if is_raw(path) else read_fits(path)
    ser = open_ser(path)
    raw = ser.read_frame(frame)
    return (raw.astype(np.float32), ser.header(frame),
            8 if ser.bpp == 1 else 16)


# ----------------------------------------------------------------------------
# Affichage (exécuté dans des threads de fond)
# ----------------------------------------------------------------------------

def subsample(a, target=500_000):
    step = max(1, int(np.sqrt(a.shape[0] * a.shape[1] / target)))
    return a[::step, ::step]


def debayer_superpixel(img, pattern):
    """Débayerisation super-pixel : rapide, image de moitié de taille."""
    h, w = img.shape
    img = img[: h - h % 2, : w - w % 2]
    quads = [img[0::2, 0::2], img[0::2, 1::2], img[1::2, 0::2], img[1::2, 1::2]]
    chans = {"R": [], "G": [], "B": []}
    for color, q in zip(pattern, quads):
        chans[color].append(q)
    r = chans["R"][0]
    g = (chans["G"][0] + chans["G"][1]) * 0.5
    b = chans["B"][0]
    return np.dstack([r, g, b])


def mtf(m, x):
    """Midtones Transfer Function (x dans [0,1], m dans ]0,1[)."""
    return ((m - 1.0) * x) / ((2.0 * m - 1.0) * x - m)


def mtf_scalar(m, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return float(mtf(m, x))


def stretch(ch, params):
    """Étire un canal 2D, renvoie du float32 dans [0,1]."""
    kind, a, b = params
    sample = subsample(ch)

    if kind == "pct":
        lo, hi = (float(v) for v in np.percentile(sample, [a, b]))
    else:
        lo, hi = float(ch.min()), float(ch.max())
    if hi <= lo:
        return np.zeros(ch.shape, dtype=np.float32)

    scale = 1.0 / (hi - lo)
    x = (ch - lo) * scale
    np.clip(x, 0.0, 1.0, out=x)
    if kind != "stf":
        return x

    s = (sample - lo) * scale
    med = float(np.median(s))
    mad = float(np.median(np.abs(s - med))) * 1.4826
    c0 = min(max(med + a * mad, 0.0), 0.99)
    bg = (med - c0) / (1.0 - c0)
    # mb tel que mtf(mb, fond) = fond cible ; 0,5 = identité
    mb = mtf_scalar(b, bg) if bg > 0 else 0.5

    x = (x - c0) * (1.0 / (1.0 - c0))
    np.clip(x, 0.0, 1.0, out=x)
    return mtf(mb, x)


def header_summary(h):
    fields = [
        ("OBJECT", "Objet", ""), ("IMAGETYP", "Type", ""),
        ("FILTER", "Filtre", ""), ("EXPTIME", "Pose", " s"),
        ("EXPOSURE", "Pose", " s"), ("GAIN", "Gain", ""),
        ("ISOSPEED", "ISO", ""),
        ("CCD-TEMP", "Temp.", " °C"), ("DATE-OBS", "Date", ""),
    ]
    parts, seen = [], set()
    for key, label, unit in fields:
        if key in h and label not in seen:
            val = h[key]
            if isinstance(val, float):
                val = f"{val:g}"
            parts.append(f"{label} {str(val).strip()}{unit}")
            seen.add(label)
    return "   ".join(parts)


def process_source(src, params, debayer):
    data, header, _ = read_source(src)

    mono = data if data.ndim == 2 else data.mean(axis=2)
    s = subsample(mono)
    med = float(np.median(s))
    noise = float(np.median(np.abs(s - med))) * 1.4826

    bayer = str(header.get("BAYERPAT", "") or "").strip().upper()
    color = data.ndim == 3
    if debayer and not color and bayer in BAYER_PATTERNS:
        data = debayer_superpixel(data, bayer)
        color = True

    if color:
        out = np.dstack([stretch(data[..., c], params) for c in range(3)])
    else:
        out = stretch(data, params)

    img8 = (out * 255.0 + 0.5).astype(np.uint8)

    # Convention FITS : 1re ligne en bas, sauf si ROWORDER = TOP-DOWN
    if str(header.get("ROWORDER", "")).strip().upper() != "TOP-DOWN":
        img8 = img8[::-1]
    img8 = np.ascontiguousarray(img8)

    h0, w0 = mono.shape
    info = (f"{src_name(src)}   |   {w0}×{h0}"
            f"{'  couleur' if color else ''}   |   "
            f"médiane {med:.1f}   bruit {noise:.1f}   |   "
            f"{header_summary(header)}")
    return {
        "image": img8,
        "header": header.tostring(sep="\n", endcard=False, padding=False),
        "info": info,
    }


def numpy_to_qimage(a):
    h, w = a.shape[:2]
    if a.ndim == 2:
        img = QImage(a.data, w, h, w, QImage.Format_Grayscale8)
    else:
        img = QImage(a.data, w, h, 3 * w, QImage.Format_RGB888)
    return img.copy()   # détache des données numpy


# ----------------------------------------------------------------------------
# Export SER et FITS
# ----------------------------------------------------------------------------

def parse_date_obs(h):
    """DATE-OBS (UTC) -> datetime naïf, ou None."""
    s = str(h.get("DATE-OBS", "") or "").strip().rstrip("Z")
    if not s:
        return None
    if "T" not in s and "TIME-OBS" in h:
        s += "T" + str(h["TIME-OBS"]).strip()
    if "." in s:
        main, frac = s.split(".", 1)
        n = 0
        while n < len(frac) and frac[n].isdigit():
            n += 1
        digits, rest = frac[:n][:6], frac[n:]       # rest = fuseau éventuel
        s = f"{main}.{digits.ljust(6, '0')}{rest}" if digits else main + rest
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def to_ser_pixels(data, depth, bitpix):
    if depth == 8:
        return np.clip(np.rint(data), 0, 255).astype(np.uint8)
    if bitpix < 0 and data.max() <= 1.0:      # FITS flottant normalisé 0–1
        data = data * 65535.0
    return np.clip(np.rint(data), 0, 65535).astype("<u2")


def write_ser(out_path, srcs, progress=None):
    """Écrit une vidéo SER (v3) à partir d'images de même taille.

    Les pixels sont écrits tels qu'ils sont stockés (pas de débayerisation,
    pas de retournement) ; la matrice de Bayer est reprise de BAYERPAT.
    Renvoie (nombre d'images, taille en octets).
    """
    data, header, bitpix = read_source(srcs[0])
    shape = data.shape
    h, w = shape[:2]
    depth = 8 if bitpix == 8 else 16
    if data.ndim == 3:
        color_id = SER_COLOR_IDS["RGB"]
    else:
        bayer = str(header.get("BAYERPAT", "") or "").strip().upper()
        color_id = SER_COLOR_IDS.get(bayer, 0)

    def text40(key):
        val = str(header.get(key, "") or "").strip()
        return val.encode("latin-1", "replace")[:40].ljust(40, b"\0")

    tmp = out_path + ".part"
    dates = []
    try:
        with open(tmp, "wb") as f:
            f.write(b"\0" * SER_HEADER.size)      # en-tête écrit à la fin
            for i, src in enumerate(srcs):
                if progress and not progress(i, src):
                    raise Cancelled()
                if i > 0:
                    data, header_i, bitpix = read_source(src)
                else:
                    header_i = header
                if data.shape != shape:
                    raise ValueError(
                        f"{src_name(src)} : dimensions {data.shape} "
                        f"différentes de la 1re image {shape}")
                f.write(to_ser_pixels(data, depth, bitpix).tobytes())
                dates.append(parse_date_obs(header_i))

            utc = local = 0
            if dates and all(d is not None for d in dates):
                f.write(np.array([to_ticks(d) for d in dates],
                                 dtype="<i8").tobytes())
                utc = to_ticks(dates[0])
                local = to_ticks(dates[0].replace(tzinfo=timezone.utc)
                                 .astimezone().replace(tzinfo=None))

            # LittleEndian = 0 : données en petit-boutiste (convention de
            # fait suivie par Siril, PIPP, AutoStakkert, SER Player…)
            hdr = SER_HEADER.pack(
                b"LUCAM-RECORDER", 0, color_id, 0, w, h, depth, len(srcs),
                text40("OBSERVER"), text40("INSTRUME"), text40("TELESCOP"),
                local, utc)
            f.seek(0)
            f.write(hdr)
        os.replace(tmp, out_path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    finally:
        open_ser.cache_clear()          # au cas où un SER aurait été remplacé
    return len(srcs), os.path.getsize(out_path)


def export_fits(out_dir, srcs, progress=None):
    """Copie les FITS, convertit les RAW et chaque image de SER en FITS.
    Les pixels sont conservés tels quels (8 ou 16 bits)."""
    done = 0
    for i, src in enumerate(srcs):
        if progress and not progress(i, src):
            raise Cancelled()
        path, frame = src
        if frame < 0 and is_raw(path):
            data, header, _ = read_raw(path)
            stem = os.path.splitext(os.path.basename(path))[0]
            dst = unique_path(os.path.join(out_dir, stem + ".fits"))
            fits.PrimaryHDU(data=data.astype(np.uint16),
                            header=header).writeto(dst)
        elif frame < 0:
            dst = unique_path(os.path.join(out_dir, os.path.basename(path)))
            shutil.copy2(path, dst)
        else:
            ser = open_ser(path)
            raw = ser.read_frame(frame)
            raw = raw.astype(np.uint8 if ser.bpp == 1 else np.uint16)
            if raw.ndim == 3:
                raw = np.moveaxis(raw, -1, 0)        # FITS : (3, H, W)
            stem = os.path.splitext(os.path.basename(path))[0]
            digits = max(4, len(str(ser.count)))
            dst = unique_path(os.path.join(
                out_dir, f"{stem}_{frame + 1:0{digits}d}.fits"))
            fits.PrimaryHDU(data=np.ascontiguousarray(raw),
                            header=ser.header(frame)).writeto(dst)
        done += 1
    return done


# ----------------------------------------------------------------------------
# Chargement en arrière-plan
# ----------------------------------------------------------------------------

class LoaderSignals(QObject):
    done = pyqtSignal(object, object, object)   # clé, résultat, erreur


class LoadJob(QRunnable):
    def __init__(self, key, params, signals):
        super().__init__()
        self.key, self.params, self.signals = key, params, signals

    def run(self):
        src, _mode, debayer = self.key
        try:
            res = process_source(src, self.params, debayer)
            self.signals.done.emit(self.key, res, None)
        except Exception as e:   # noqa: BLE001
            self.signals.done.emit(self.key, None, str(e))


# ----------------------------------------------------------------------------
# Widgets
# ----------------------------------------------------------------------------

class ImageView(QGraphicsView):
    """Vue avec zoom molette, déplacement à la souris, double-clic = ajuster."""

    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.pix = QGraphicsPixmapItem()
        self.pix.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self.pix)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(24, 24, 26))
        self.setFocusPolicy(Qt.NoFocus)
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.fit_mode = True

    def set_image(self, qimg):
        self.pix.setPixmap(QPixmap.fromImage(qimg))
        self._scene.setSceneRect(self.pix.boundingRect())
        if self.fit_mode:
            self.fit()

    def clear(self):
        self.pix.setPixmap(QPixmap())

    def fit(self):
        self.fit_mode = True
        if not self.pix.pixmap().isNull():
            self.fitInView(self.pix, Qt.KeepAspectRatio)

    def zoom_100(self):
        self.fit_mode = False
        self.resetTransform()

    def wheelEvent(self, e):
        factor = 1.25 if e.angleDelta().y() > 0 else 0.8
        self.fit_mode = False
        self.scale(factor, factor)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fit_mode:
            self.fit()

    def mouseDoubleClickEvent(self, e):
        self.fit()


class FileList(QListWidget):
    toggleRequested = pyqtSignal()

    def keyPressEvent(self, e):
        if (e.key() in (Qt.Key_Space, Qt.Key_Delete, Qt.Key_X)
                and e.modifiers() == Qt.NoModifier):
            self.toggleRequested.emit()
            return
        super().keyPressEvent(e)


# ----------------------------------------------------------------------------
# Fenêtre principale
# ----------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 860)
        self.setAcceptDrops(True)

        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(2, min(4, QThread.idealThreadCount())))
        self.signals = LoaderSignals()
        self.signals.done.connect(self.on_loaded)
        self.cache = OrderedDict()
        self.pending = set()
        self.current_src = None
        self.shown_key = None            # clé de l'image actuellement affichée

        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self.play_step)

        self._build_ui()
        self._build_menus()
        self.update_count()
        self.statusBar().showMessage(
            "Glissez-déposez des fichiers FITS, SER ou CR2, ou un dossier, "
            "ou utilisez Fichier ▸ Ajouter des fichiers")

    # --- interface -----------------------------------------------------------

    def _build_ui(self):
        self.list = FileList()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setUniformItemSizes(True)      # rapide avec un long SER
        self.list.setAcceptDrops(False)
        self.list.viewport().setAcceptDrops(False)
        self.list.currentItemChanged.connect(self.on_current_changed)
        self.list.toggleRequested.connect(self.toggle_reject)

        hint = QLabel("Espace / Suppr / X : rejeter ou garder\n"
                      "↑ ↓ : image précédente / suivante\n"
                      "P : lire / mettre en pause")
        hint.setStyleSheet("color: gray;")
        self.count_label = QLabel()

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(4, 4, 4, 4)
        lv.addWidget(self.list)
        lv.addWidget(self.count_label)
        lv.addWidget(hint)

        self.view = ImageView()

        self.header_view = QPlainTextEdit()
        self.header_view.setReadOnly(True)
        self.header_view.setAcceptDrops(False)
        self.header_view.viewport().setAcceptDrops(False)
        self.header_view.setFocusPolicy(Qt.ClickFocus)
        self.header_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.TypeWriter)
        mono.setPointSize(9)
        self.header_view.setFont(mono)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.view)
        splitter.addWidget(self.header_view)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([290, 850, 330])
        self.setCentralWidget(splitter)

        tb = self.addToolBar("Affichage")
        tb.setMovable(False)
        tb.addWidget(QLabel(" Étirement : "))
        self.stretch_combo = QComboBox()
        self.stretch_combo.addItems(list(STRETCH_MODES.keys()))
        self.stretch_combo.setFocusPolicy(Qt.NoFocus)
        self.stretch_combo.currentIndexChanged.connect(self.refresh_current)
        tb.addWidget(self.stretch_combo)
        tb.addSeparator()
        self.debayer_cb = QCheckBox("Débayeriser (couleur)")
        self.debayer_cb.setFocusPolicy(Qt.NoFocus)
        self.debayer_cb.setToolTip(
            "Utilise la matrice de Bayer des images de caméras couleur")
        self.debayer_cb.toggled.connect(self.refresh_current)
        tb.addWidget(self.debayer_cb)
        tb.addSeparator()
        self.advance_cb = QCheckBox("Passer à l'image suivante après marquage")
        self.advance_cb.setFocusPolicy(Qt.NoFocus)
        self.advance_cb.setChecked(True)
        tb.addWidget(self.advance_cb)
        tb.addSeparator()

        self.play_action = QAction("▶ Lire", self)
        self.play_action.setCheckable(True)
        self.play_action.setShortcut(QKeySequence("P"))
        self.play_action.setToolTip("Lire la liste comme une vidéo (P)")
        self.play_action.toggled.connect(self.set_playing)
        tb.addAction(self.play_action)
        tb.addWidget(QLabel(" Vitesse : "))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(10)
        self.fps_spin.setSuffix(" im/s")
        self.fps_spin.setFocusPolicy(Qt.ClickFocus)
        self.fps_spin.valueChanged.connect(self.update_play_speed)
        tb.addWidget(self.fps_spin)

        self.list.setFocus()

    def _build_menus(self):
        mb = self.menuBar()

        m = mb.addMenu("&Fichier")
        self._action(m, "Ajouter des fichiers FITS, SER ou CR2…",
                     self.add_files_dialog, QKeySequence.Open)
        self._action(m, "Ajouter un dossier…", self.add_folder_dialog, "Ctrl+D")
        m.addSeparator()
        self._action(m, "Retirer la sélection de la liste", self.remove_selected)
        self._action(m, "Vider la liste", self.clear_list)
        m.addSeparator()
        self._action(m, "Quitter", self.close, QKeySequence.Quit)

        m = mb.addMenu("&Tri")
        self._action(m, "Rejeter / garder la sélection   (Espace)",
                     self.toggle_reject)
        self._action(m, "Garder toutes les images", self.unmark_all)
        m.addSeparator()
        self._action(m, f"Déplacer les fichiers rejetés dans « {REJECT_DIR} »…",
                     self.move_rejected, "Ctrl+M")
        self._action(m, "Supprimer définitivement les fichiers rejetés…",
                     self.delete_rejected)

        m = mb.addMenu("&Export")
        self._action(m, "Créer un SER avec les images sélectionnées…",
                     lambda: self.export_ser(selected_only=True), "Ctrl+E")
        self._action(m, "Créer un SER avec toutes les images gardées…",
                     lambda: self.export_ser(selected_only=False),
                     "Ctrl+Shift+E")
        m.addSeparator()
        self._action(m, "Exporter en FITS les images sélectionnées…",
                     lambda: self.export_fits(selected_only=True))
        self._action(m, "Exporter en FITS toutes les images gardées…",
                     lambda: self.export_fits(selected_only=False))

        m = mb.addMenu("&Affichage")
        m.addAction(self.play_action)
        m.addSeparator()
        self._action(m, "Ajuster à la fenêtre", self.view.fit, "F")
        self._action(m, "Zoom 100 %", self.view.zoom_100, "1")
        self.flip_action = self._action(m, "Retourner verticalement",
                                        self.redisplay, "V")
        self.flip_action.setCheckable(True)

    def _action(self, menu, text, slot, shortcut=None):
        act = QAction(text, self)
        if shortcut is not None:
            act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(lambda checked=False: slot())
        menu.addAction(act)
        return act

    # --- gestion de la liste -------------------------------------------------

    def all_items(self):
        return [self.list.item(i) for i in range(self.list.count())]

    def _add_item(self, src, text):
        item = QListWidgetItem(text)
        item.setData(ROLE_SRC, src)
        item.setData(ROLE_REJ, False)
        item.setToolTip(src[0] if src[1] < 0
                        else f"{src[0]}\nimage {src[1] + 1}")
        self.list.addItem(item)

    def add_paths(self, paths):
        files = []
        for p in paths:
            if os.path.isdir(p):
                for name in sorted(os.listdir(p)):
                    full = os.path.join(p, name)
                    if os.path.isfile(full) and is_supported(name):
                        files.append(full)
            elif os.path.isfile(p) and is_supported(p):
                files.append(p)

        existing = {it.data(ROLE_SRC) for it in self.all_items()}
        added, errors = 0, []
        self.list.setUpdatesEnabled(False)
        try:
            for f in files:
                f = os.path.abspath(f)
                name = os.path.basename(f)
                if is_ser(f):
                    try:
                        ser = open_ser(f)
                    except (OSError, ValueError) as e:
                        errors.append(f"{name} : {e}")
                        continue
                    digits = len(str(ser.count))
                    for i in range(ser.count):
                        if (f, i) not in existing:
                            self._add_item((f, i), f"{name}  #{i + 1:0{digits}d}")
                            added += 1
                elif (f, -1) not in existing:
                    self._add_item((f, -1), name)
                    added += 1
            self.list.sortItems()
        finally:
            self.list.setUpdatesEnabled(True)

        self.update_count()
        if added and self.list.currentItem() is None:
            self.list.setCurrentRow(0)
        if errors:
            QMessageBox.warning(self, APP_NAME, "Fichiers illisibles :\n"
                                + "\n".join(errors[:20]))
        elif not added and paths:
            self.statusBar().showMessage(
                "Aucune nouvelle image trouvée", 4000)

    def add_files_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Ajouter des fichiers FITS, SER ou CR2", "",
            "Images FITS, vidéos SER, RAW Canon (*.fit *.fits *.fts *.ser "
            "*.cr2 *.FIT *.FITS *.FTS *.SER *.CR2 *.gz);;Tous les fichiers (*)")
        if files:
            self.add_paths(files)

    def add_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Ajouter un dossier")
        if d:
            self.add_paths([d])

    def remove_selected(self):
        self.list.setUpdatesEnabled(False)
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self.list.setUpdatesEnabled(True)
        self.update_count()

    def clear_list(self):
        self.play_action.setChecked(False)
        self.list.clear()
        self.cache.clear()
        self.current_src = None
        self.shown_key = None
        self.view.clear()
        self.header_view.clear()
        self.update_count()

    def update_count(self):
        n = self.list.count()
        r = sum(1 for it in self.all_items() if it.data(ROLE_REJ))
        self.count_label.setText(
            f"{n} image(s)   —   {n - r} gardée(s), {r} rejetée(s)")

    # --- marquage ------------------------------------------------------------

    def set_rejected(self, item, flag):
        item.setData(ROLE_REJ, flag)
        font = item.font()
        font.setStrikeOut(flag)
        item.setFont(font)
        item.setData(Qt.ForegroundRole, QColor("#e05555") if flag else None)

    def toggle_reject(self):
        items = self.list.selectedItems()
        if not items and self.list.currentItem():
            items = [self.list.currentItem()]
        if not items:
            return
        new_state = not all(it.data(ROLE_REJ) for it in items)
        for it in items:
            self.set_rejected(it, new_state)
        self.update_count()
        if (len(items) == 1 and self.advance_cb.isChecked()
                and not self.play_action.isChecked()):
            row = self.list.row(items[0])
            if row + 1 < self.list.count():
                self.list.setCurrentRow(row + 1)

    def unmark_all(self):
        for it in self.all_items():
            if it.data(ROLE_REJ):
                self.set_rejected(it, False)
        self.update_count()

    def rejected_fits_items(self):
        """Images rejetées qui sont des fichiers FITS, et nombre d'images
        rejetées issues de SER (qui ne sont pas des fichiers séparés)."""
        rej = [it for it in self.all_items() if it.data(ROLE_REJ)]
        files = [it for it in rej if it.data(ROLE_SRC)[1] < 0]
        return files, len(rej) - len(files)

    def _ser_note(self, n_ser):
        if not n_ser:
            return ""
        return (f"\n\n{n_ser} image(s) rejetée(s) proviennent de vidéos SER : "
                "ce ne sont pas des fichiers séparés, elles restent dans la "
                "liste. Pour les écarter, exportez les images gardées en SER "
                "ou en FITS (menu Export).")

    def move_rejected(self):
        rej, n_ser = self.rejected_fits_items()
        if not rej:
            QMessageBox.information(self, APP_NAME, "Aucun fichier rejeté."
                                    + self._ser_note(n_ser))
            return
        if QMessageBox.question(
                self, APP_NAME,
                f"Déplacer {len(rej)} fichier(s) rejeté(s) dans un "
                f"sous-dossier « {REJECT_DIR} » placé à côté de chaque fichier ?"
                + self._ser_note(n_ser)
        ) != QMessageBox.Yes:
            return
        done, errors = 0, []
        for it in rej:
            src = it.data(ROLE_SRC)[0]
            dst_dir = os.path.join(os.path.dirname(src), REJECT_DIR)
            try:
                os.makedirs(dst_dir, exist_ok=True)
                shutil.move(src, unique_path(
                    os.path.join(dst_dir, os.path.basename(src))))
                self.forget(it)
                done += 1
            except OSError as e:
                errors.append(f"{os.path.basename(src)} : {e}")
        self.report(f"{done} fichier(s) déplacé(s).", errors)

    def delete_rejected(self):
        rej, n_ser = self.rejected_fits_items()
        if not rej:
            QMessageBox.information(self, APP_NAME, "Aucun fichier rejeté."
                                    + self._ser_note(n_ser))
            return
        box = QMessageBox(QMessageBox.Warning, APP_NAME,
                          f"Supprimer définitivement {len(rej)} fichier(s) "
                          f"du disque ?\nCette action est irréversible."
                          + self._ser_note(n_ser),
                          QMessageBox.Yes | QMessageBox.No, self)
        box.setDefaultButton(QMessageBox.No)
        if box.exec_() != QMessageBox.Yes:
            return
        done, errors = 0, []
        for it in rej:
            path = it.data(ROLE_SRC)[0]
            try:
                os.remove(path)
                self.forget(it)
                done += 1
            except OSError as e:
                errors.append(f"{os.path.basename(path)} : {e}")
        self.report(f"{done} fichier(s) supprimé(s).", errors)

    def forget(self, item):
        src = item.data(ROLE_SRC)
        for k in [k for k in self.cache if k[0] == src]:
            del self.cache[k]
        self.list.takeItem(self.list.row(item))

    def report(self, msg, errors):
        self.update_count()
        if errors:
            QMessageBox.warning(self, APP_NAME,
                                msg + "\n\nErreurs :\n" + "\n".join(errors[:20]))
        else:
            self.statusBar().showMessage(msg, 6000)

    # --- export --------------------------------------------------------------

    def chosen_sources(self, selected_only):
        if selected_only:
            items = sorted(self.list.selectedItems(), key=self.list.row)
            if not items:
                QMessageBox.information(
                    self, APP_NAME,
                    "Aucune image sélectionnée.\nSélectionnez des images avec "
                    "Ctrl+clic ou Maj+clic dans la liste.")
                return None
        else:
            items = [it for it in self.all_items() if not it.data(ROLE_REJ)]
            if not items:
                QMessageBox.information(self, APP_NAME,
                                        "Aucune image gardée dans la liste.")
                return None
        return [it.data(ROLE_SRC) for it in items]

    def run_with_progress(self, title, srcs, work):
        """Exécute work(progress) avec une barre de progression annulable.
        Renvoie le résultat de work, ou None en cas d'annulation ou d'erreur."""
        self.play_action.setChecked(False)
        prog = QProgressDialog(title, "Annuler", 0, len(srcs), self)
        prog.setWindowTitle(APP_NAME)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)

        def progress(i, src):
            prog.setValue(i)
            prog.setLabelText(f"{title}\nImage {i + 1} / {len(srcs)} : "
                              f"{src_name(src)}")
            QApplication.processEvents()
            return not prog.wasCanceled()

        result, error, cancelled = None, None, False
        try:
            result = work(progress)
        except Cancelled:
            cancelled = True
        except Exception as e:   # noqa: BLE001
            error = e
        finally:
            prog.setValue(len(srcs))
            prog.close()

        if cancelled:
            self.statusBar().showMessage(f"{title} : annulé", 5000)
        elif error is not None:
            QMessageBox.warning(self, APP_NAME, f"{title} : échec.\n{error}")
        return result

    def export_ser(self, selected_only):
        srcs = self.chosen_sources(selected_only)
        if not srcs:
            return
        first_path, first_frame = srcs[0]
        folder = os.path.dirname(first_path)
        if first_frame >= 0:
            stem = os.path.splitext(os.path.basename(first_path))[0]
            default = os.path.join(folder, f"{stem}_selection.ser")
        else:
            default = os.path.join(folder, os.path.basename(folder) + ".ser")
        out, _ = QFileDialog.getSaveFileName(
            self, f"Créer un SER ({len(srcs)} images)", default,
            "Vidéo SER (*.ser)")
        if not out:
            return
        if not out.lower().endswith(".ser"):
            out += ".ser"
        if os.path.abspath(out) in {os.path.abspath(p) for p, _ in srcs}:
            QMessageBox.warning(self, APP_NAME,
                                "Le SER ne peut pas remplacer une vidéo dont "
                                "il reprend les images. Choisissez un autre nom.")
            return

        res = self.run_with_progress(
            "Création du SER", srcs,
            lambda progress: write_ser(out, srcs, progress))
        if res:
            n, size = res
            QMessageBox.information(
                self, APP_NAME,
                f"{n} images enregistrées dans\n{out}\n({size / 1e6:.0f} Mo)")

    def export_fits(self, selected_only):
        srcs = self.chosen_sources(selected_only)
        if not srcs:
            return
        out_dir = QFileDialog.getExistingDirectory(
            self, f"Dossier où exporter {len(srcs)} image(s) en FITS",
            os.path.dirname(srcs[0][0]))
        if not out_dir:
            return
        n = self.run_with_progress(
            "Export en FITS", srcs,
            lambda progress: export_fits(out_dir, srcs, progress))
        if n:
            QMessageBox.information(
                self, APP_NAME, f"{n} fichier(s) FITS enregistré(s) dans\n"
                                f"{out_dir}")

    # --- lecture vidéo -------------------------------------------------------

    def set_playing(self, on):
        if on and self.list.count() == 0:
            self.play_action.setChecked(False)
            return
        self.play_action.setText("⏸ Pause" if on else "▶ Lire")
        if on:
            if self.list.currentRow() < 0:
                self.list.setCurrentRow(0)
            self.update_play_speed()
        else:
            self.play_timer.stop()

    def update_play_speed(self, *_):
        if self.play_action.isChecked():
            self.play_timer.start(max(1, int(1000 / self.fps_spin.value())))

    def next_play_row(self, row):
        """Image suivante pour la lecture : saute les rejetées et boucle.
        Pour un SER, la lecture reste dans la même vidéo."""
        n = self.list.count()
        if n == 0:
            return None
        if row < 0:
            return 0
        path, frame = self.list.item(row).data(ROLE_SRC)
        if frame >= 0:
            def in_group(r):
                return self.list.item(r).data(ROLE_SRC)[0] == path
        else:
            def in_group(_r):
                return True
        r = row
        for _ in range(n):
            r += 1
            if r >= n or not in_group(r):          # retour au début
                r = row
                while r > 0 and in_group(r - 1):
                    r -= 1
            if not self.list.item(r).data(ROLE_REJ):
                return r
        return None

    def play_step(self):
        if (self.current_src is not None
                and self.shown_key != self.make_key(self.current_src)):
            return                     # image courante pas encore prête
        nxt = self.next_play_row(self.list.currentRow())
        if nxt is None:
            self.play_action.setChecked(False)
        elif nxt != self.list.currentRow():
            self.list.setCurrentRow(nxt)

    # --- affichage -----------------------------------------------------------

    def make_key(self, src):
        return (src, self.stretch_combo.currentText(),
                self.debayer_cb.isChecked())

    def submit(self, src, priority=0):
        key = self.make_key(src)
        if key in self.cache or key in self.pending:
            return
        self.pending.add(key)
        self.pool.start(LoadJob(key, STRETCH_MODES[key[1]], self.signals),
                        priority)

    def on_current_changed(self, cur, _prev):
        if cur is None:
            self.current_src = None
            self.view.clear()
            self.header_view.clear()
            return
        self.show_src(cur.data(ROLE_SRC))

    def show_src(self, src):
        self.current_src = src
        key = self.make_key(src)
        if key in self.cache:
            self.cache.move_to_end(key)
            self.display(key, self.cache[key])
        else:
            if not self.play_action.isChecked():
                self.statusBar().showMessage(f"Chargement de {src_name(src)}…")
            self.submit(src, priority=10)
        self.prefetch()

    def prefetch(self):
        row = self.list.currentRow()
        if row < 0:
            return
        if self.play_action.isChecked():
            rows, r = [], row
            for _ in range(4):
                r = self.next_play_row(r)
                if r is None or r == row or r in rows:
                    break
                rows.append(r)
        else:
            rows = [row + 1, row + 2, row - 1]
        for r in rows:
            if 0 <= r < self.list.count():
                self.submit(self.list.item(r).data(ROLE_SRC))

    def on_loaded(self, key, result, error):
        self.pending.discard(key)
        is_current = (self.current_src is not None
                      and key == self.make_key(self.current_src))
        if error:
            if is_current:
                self.shown_key = key   # la lecture vidéo ne reste pas bloquée
                self.view.clear()
                self.header_view.setPlainText(f"Lecture impossible :\n{error}")
                self.statusBar().showMessage(
                    f"{src_name(key[0])} : lecture impossible ({error})")
            return
        self.cache[key] = result
        self.cache.move_to_end(key)
        while len(self.cache) > CACHE_MAX:
            self.cache.popitem(last=False)
        if is_current:
            self.display(key, result)

    def display(self, key, result):
        qimg = numpy_to_qimage(result["image"])
        if self.flip_action.isChecked():
            qimg = qimg.mirrored(False, True)
        self.view.set_image(qimg)
        self.header_view.setPlainText(result["header"])
        self.statusBar().showMessage(result["info"])
        self.shown_key = key

    def redisplay(self):
        if self.current_src:
            key = self.make_key(self.current_src)
            if key in self.cache:
                self.display(key, self.cache[key])

    def refresh_current(self, *_):
        if self.current_src:
            self.show_src(self.current_src)
        self.list.setFocus()

    # --- glisser-déposer -----------------------------------------------------

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)
            e.acceptProposedAction()

    def closeEvent(self, e):
        self.play_timer.stop()
        self.pool.clear()
        self.pool.waitForDone(2000)
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("fitsview")    # associe la fenêtre à fitsview.desktop
    here = os.path.dirname(os.path.abspath(__file__))
    icon = QIcon.fromTheme("fitsview")
    for name in ("fitsview.svg", "fitsview.png"):
        if icon.isNull() and os.path.exists(os.path.join(here, name)):
            icon = QIcon(os.path.join(here, name))
    app.setWindowIcon(icon)
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1:
        win.add_paths(sys.argv[1:])
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
