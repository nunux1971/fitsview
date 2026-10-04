# fitsview — notice d'utilisation

Octobre 2026

## À quoi sert fitsview

fitsview sert à passer en revue ses brutes FITS ou RAW Canon (CR2), ou les images d'une vidéo SER, une par une, à écarter les mauvaises et à préparer le reste pour le prétraitement. Il ne modifie jamais le contenu de vos fichiers : il les affiche, les déplace ou les assemble en vidéo SER.

Une séance typique se déroule en quatre temps :

1. Glisser le dossier de la nuit dans la fenêtre.
2. Faire défiler les images au clavier et rejeter celles qui sont ratées (nuages, filé, buée, passage de satellite, avion).
3. Ranger les rejetées dans un sous-dossier « rejetes ».
4. Lancer le prétraitement dans Siril ou PixInsight sur les images restantes, ou créer un SER pour AutoStakkert ou PIPP.

## Installation sous Linux

fitsview est un script Python. Il a besoin de PyQt5, numpy et astropy, à installer une seule fois avec le gestionnaire de paquets de votre distribution.

| Distribution | Commande |
| --- | --- |
| Debian, Ubuntu, Linux Mint | `sudo apt install python3-pyqt5 python3-numpy python3-astropy` |
| Fedora | `sudo dnf install python3-qt5 python3-numpy python3-astropy` |
| Arch, Manjaro | `sudo pacman -S python-pyqt5 python-numpy python-astropy` |

Pour ouvrir les fichiers RAW Canon (CR2), installez en plus le petit programme **dcraw** : `sudo apt install dcraw` sous Debian, Ubuntu ou Mint, `sudo dnf install dcraw` sous Fedora, `sudo pacman -S dcraw` sous Arch. Si le module Python **rawpy** est installé, fitsview l'utilise à la place : il est un peu plus rapide, mais n'est pas proposé par toutes les distributions.

### Lancer le programme

Placez `fitsview.py` et `fitsview.svg` dans un même dossier, par exemple `~/Applications/fitsview/`. Rendez le script exécutable puis lancez-le :

```bash
cd ~/Applications/fitsview
chmod +x fitsview.py
./fitsview.py
```

Vous pouvez aussi lui donner directement des fichiers ou un dossier : `./fitsview.py ~/Astro/2026-10-02/Lights/`.

### Ajouter fitsview au menu des applications

Le script `installer.sh` fourni avec l'icône copie le programme dans `~/.local/share/fitsview/`, installe l'icône et crée l'entrée de menu. Lancez-le depuis le dossier qui contient les trois fichiers :

```bash
sh installer.sh
```

fitsview apparaît ensuite dans le menu, rubrique Graphisme ou Sciences selon le bureau. Il figure aussi dans le menu « Ouvrir avec » d'un clic droit sur un fichier FITS. Si l'icône n'apparaît pas tout de suite, fermez puis rouvrez votre session.

Pour désinstaller : `sh installer.sh --retirer`.

## La fenêtre et l'ajout d'images

La fenêtre est divisée en trois colonnes, dont vous pouvez régler la largeur en tirant sur leurs séparations.

| Zone | Contenu |
| --- | --- |
| Gauche | La liste des fichiers, le compteur d'images gardées et rejetées, un rappel des touches |
| Centre | L'image affichée, ajustée pour être visible |
| Droite | L'en-tête FITS complet de l'image affichée |
| Barre du haut | Le choix de l'étirement, la débayerisation et l'avance automatique |
| Barre du bas | Un résumé : nom, dimensions, médiane du fond, bruit, objet, filtre, pose, gain, température, date |

### Ajouter des images

Le plus simple est de glisser des fichiers ou un dossier entier depuis votre gestionnaire de fichiers et de les déposer n'importe où sur la fenêtre. Vous pouvez aussi passer par **Fichier ▸ Ajouter des fichiers FITS, SER ou CR2** (Ctrl+O) ou **Fichier ▸ Ajouter un dossier** (Ctrl+D).

Quand vous ajoutez un dossier, fitsview prend les fichiers FITS, les vidéos SER et les RAW CR2 qu'il contient directement, sans descendre dans les sous-dossiers. Vos darks, flats ou offsets rangés à part ne se mélangent donc pas aux brutes. Les extensions reconnues sont `.fit`, `.fits`, `.fts` et leurs versions compressées `.gz`.

La liste est triée par nom de fichier, ce qui correspond à l'ordre chronologique avec la plupart des logiciels d'acquisition. Un fichier déjà présent n'est jamais ajouté deux fois. Survolez un nom pour voir son chemin complet.

**Fichier ▸ Retirer la sélection de la liste** et **Fichier ▸ Vider la liste** enlèvent des noms de la liste sans toucher aux fichiers sur le disque.

## Afficher et ajuster les images

Cliquez sur un nom pour afficher l'image, puis utilisez les flèches haut et bas pour passer de l'une à l'autre. Les images voisines sont préparées à l'avance, le défilement reste donc fluide même avec un grand capteur.

### Choisir l'étirement

Une brute est presque noire à l'écran : le signal du ciel n'occupe qu'une petite partie de la dynamique. fitsview l'étire automatiquement pour la rendre lisible. L'étirement ne sert qu'à l'affichage, vos fichiers restent intacts.

| Étirement | Effet | Quand l'utiliser |
| --- | --- | --- |
| Auto-stretch (STF) | Fond de ciel amené à 25 % de gris, comme le STF de PixInsight | Par défaut, pour la plupart des images |
| Auto-stretch doux | Fond plus sombre (15 %), moins de bruit visible | Pour juger les étoiles et les cœurs brillants |
| Auto-stretch fort | Fond plus clair (40 %) | Pour repérer voiles nuageux, gradients et traînées faibles |
| Percentiles 0,5 – 99,8 % | Étalement linéaire entre deux seuils | Pour comparer des images entre elles sans effet de l'étirement |
| Linéaire min – max | Aucun étirement | Pour voir l'image telle qu'elle est, souvent très sombre |

Chaque image est étirée selon ses propres statistiques. Une image voilée par un nuage peut donc paraître presque normale : surveillez la valeur de la médiane dans la barre du bas, qui monte nettement quand le ciel s'éclaircit.

### Images couleur

Avec une caméra couleur, les brutes s'affichent en noir et blanc avec une fine trame en damier. Cochez **Débayeriser (couleur)** pour les voir en couleur. fitsview lit la matrice de Bayer dans le mot-clé BAYERPAT de l'en-tête ; sans ce mot-clé, l'image reste en noir et blanc. Chaque couleur est étirée séparément, ce qui neutralise la dominante verte habituelle.

### Zoomer et se déplacer

- **Molette de la souris** : zoomer ou dézoomer à l'endroit pointé.
- **Cliquer-glisser** : se déplacer dans l'image.
- **Double-clic** ou touche **F** : ajuster l'image à la fenêtre.
- Touche **1** : afficher à 100 %, un pixel de l'image pour un pixel de l'écran.

Le zoom est conservé quand vous changez d'image. Zoomez sur un coin, puis faites défiler la série : vous voyez tout de suite si les étoiles s'allongent ou se dédoublent.

### Retourner l'image

Les logiciels ne s'accordent pas sur le sens des lignes dans un fichier FITS. fitsview suit le mot-clé ROWORDER quand il est présent. Si une image apparaît à l'envers par rapport à Siril, appuyez sur **V** ou utilisez **Affichage ▸ Retourner verticalement**.

## Fichiers RAW Canon (CR2)

Les fichiers CR2 de vos reflex Canon s'ajoutent, s'affichent et se trient exactement comme des FITS. fitsview lit les données brutes du capteur, sans traitement : c'est ce qu'il faut pour juger une brute avant le prétraitement.

- **Couleur** : un CR2 est une image en mosaïque de Bayer. Cochez **Débayeriser (couleur)** pour la voir en couleur ; la matrice est lue dans le fichier.
- **Informations** : le temps de pose, la sensibilité ISO, le modèle de boîtier, la focale et la date sont repris des données EXIF. Ils s'affichent dans la barre du bas et dans la colonne de droite.
- **Rangement** : un CR2 rejeté se déplace dans « rejetes » comme un FITS.
- **Export** : les CR2 peuvent être assemblés en vidéo SER ou convertis en fichiers FITS 16 bits (menu Export), par exemple pour un logiciel qui ne lit pas les RAW.

La date EXIF est celle de l'horloge de l'appareil, souvent réglée à l'heure locale et non en temps universel. Vérifiez-la avant de vous en servir pour des mesures précises.

Le décodage d'un CR2 prend environ une demi-seconde. Le défilement reste fluide grâce au préchargement, mais la lecture vidéo d'une série de CR2 sera plus lente qu'avec des FITS.

## Vidéos SER

Une vidéo SER s'ajoute comme un fichier FITS, par glisser-déposer ou par le menu Fichier. Chacune de ses images apparaît alors dans la liste sous la forme `jupiter.ser  #0001`, `#0002`… et se trie exactement comme un FITS : affichage, étirement, débayerisation, zoom et rejet fonctionnent de la même façon.

Les dimensions, la profondeur (8 ou 16 bits), la matrice de Bayer et l'heure de chaque image sont lues dans le fichier. Elles s'affichent dans la colonne de droite, avec le numéro de l'image dans la vidéo.

### Lire la vidéo

Appuyez sur **P** ou cliquez sur **▶ Lire** dans la barre du haut pour faire défiler les images comme une vidéo. Le champ **Vitesse** règle la cadence, de 1 à 60 images par seconde. Appuyez de nouveau sur P pour mettre en pause.

- La lecture tourne en boucle sur la vidéo en cours ; avec des FITS, elle parcourt toute la liste.
- Les images rejetées sont sautées : vous voyez directement ce que donnera la vidéo après tri.
- Vous pouvez rejeter une image pendant la lecture avec Espace.
- Le zoom est conservé, ce qui permet de suivre la turbulence sur un détail de la planète.

Avec de très grandes images, la lecture peut être plus lente que la vitesse demandée : fitsview attend que chaque image soit prête plutôt que d'en sauter.

### Écarter des images d'un SER

Les images d'un SER ne sont pas des fichiers séparés : on ne peut pas les déplacer dans « rejetes ». Pour garder seulement les bonnes, exportez-les une fois le tri fini, en nouvelle vidéo SER ou en fichiers FITS (section suivante). Le SER d'origine n'est jamais modifié.

## Trier les images

Pour rejeter l'image affichée, appuyez sur **Espace**, **Suppr** ou **X**. Son nom passe en rouge barré et fitsview affiche aussitôt l'image suivante. Appuyez de nouveau sur la même touche pour la récupérer.

Ce marquage n'agit pas encore sur le disque : tant que vous n'avez pas rangé ou supprimé les rejetées, vous pouvez changer d'avis autant de fois que nécessaire. Le compteur sous la liste indique en permanence le nombre d'images gardées et rejetées.

### Trier plusieurs images d'un coup

Sélectionnez plusieurs noms avec Ctrl+clic ou Maj+clic, puis appuyez sur Espace. Si au moins une image de la sélection est gardée, toutes sont rejetées ; si toutes étaient déjà rejetées, elles sont toutes récupérées. C'est pratique pour écarter d'un seul geste une série gâchée par un passage nuageux.

**Tri ▸ Garder toutes les images** annule tous les marquages.

### Avance automatique

La case **Passer à l'image suivante après marquage** est cochée par défaut. Il suffit alors de garder un doigt sur la flèche bas et l'autre sur Espace. Décochez-la si vous préférez avancer vous-même.

### Ranger ou supprimer les rejetées

Une fois le tri terminé, **Tri ▸ Déplacer les fichiers rejetés dans « rejetes »** (Ctrl+M) déplace chaque image rejetée dans un sous-dossier `rejetes` créé à côté d'elle. Rien n'est perdu : vous pouvez récupérer un fichier à la main plus tard. Les images déplacées disparaissent de la liste, et le dossier d'origine ne contient plus que les bonnes images, prêtes pour Siril ou PixInsight.

**Tri ▸ Supprimer définitivement les fichiers rejetés** efface les fichiers du disque, sans passer par la corbeille. fitsview demande une confirmation, avec « Non » proposé par défaut. Préférez le déplacement tant que vous n'êtes pas sûr de vous.

Ces deux commandes concernent les fichiers FITS et CR2. Les images rejetées issues d'une vidéo SER restent dans la liste ; écartez-les en exportant les images gardées.

## Créer une vidéo SER

Le menu **Export** assemble des images (FITS ou images d'un autre SER) en un seul fichier SER, le format vidéo lu par Siril, AutoStakkert, PIPP ou SER Player. Il propose deux choix, qui diffèrent seulement par les images retenues.

| Commande | Images retenues | Usage courant |
| --- | --- | --- |
| Créer un SER avec toutes les images gardées (Ctrl+Maj+E) | Toute la liste, sauf les images rejetées | Après un tri, pour empiler tout le reste |
| Créer un SER avec les images sélectionnées (Ctrl+E) | Seulement les noms surlignés à la souris | Pour isoler une partie de la séance |

Attention : avec les images sélectionnées, c'est la sélection qui décide. Une image rejetée mais surlignée sera incluse.

fitsview propose d'enregistrer le fichier à côté des images, sous le nom du dossier. Une barre de progression permet d'annuler à tout moment ; le fichier n'apparaît qu'une fois la création terminée. La taille finale s'affiche à la fin.

### Ce que contient le SER

- **Les pixels bruts**, sans étirement ni débayerisation : l'étirement choisi à l'écran n'a aucun effet sur le SER.
- **La matrice de Bayer** (RGGB, GRBG…) reprise du mot-clé BAYERPAT, pour que le logiciel de traitement débayerise correctement. Les FITS déjà en couleur sont écrits en RGB.
- **La profondeur** : 8 bits si les FITS sont en 8 bits, 16 bits sinon. Les FITS en virgule flottante compris entre 0 et 1 sont ramenés sur l'échelle 0 – 65535.
- **Les dates de prise de vue** tirées de DATE-OBS, quand toutes les images en ont une.
- **Le télescope, l'instrument et l'observateur**, s'ils figurent dans l'en-tête de la première image.

Toutes les images doivent avoir les mêmes dimensions. Si l'une diffère, par exemple à cause d'un binning différent, la création s'arrête et le fichier fautif est indiqué.

Un SER n'est pas compressé : il pèse à peu près la somme des FITS qu'il contient. Prévoyez la place sur le disque avant d'assembler une longue série.

### Exporter en fichiers FITS

**Export ▸ Exporter en FITS les images sélectionnées** et **Export ▸ Exporter en FITS toutes les images gardées** enregistrent les images choisies dans un dossier que vous indiquez. Comme pour le SER, la première commande suit la sélection et la seconde prend tout sauf les rejetées.

Chaque image issue d'une vidéo SER devient un fichier FITS nommé d'après la vidéo et son numéro, par exemple `jupiter_0042.fits`. Les pixels sont conservés tels quels, en 8 ou 16 bits, avec la matrice de Bayer, l'heure de prise de vue et le numéro de l'image dans l'en-tête. Les CR2 sont convertis en FITS 16 bits portant le même nom, avec la matrice de Bayer et les informations EXIF dans l'en-tête. Les images déjà en FITS sont simplement copiées.

Cette commande sert surtout à extraire quelques images d'un SER, ou à le convertir entièrement en FITS pour un logiciel qui ne lit pas les vidéos.

## Raccourcis clavier

| Touche | Action |
| --- | --- |
| ↑ / ↓ | Image précédente / suivante |
| Début / Fin | Première / dernière image |
| Espace, Suppr ou X | Rejeter ou récupérer l'image (ou la sélection) |
| Ctrl+clic, Maj+clic | Sélectionner plusieurs images |
| P | Lire la liste comme une vidéo / mettre en pause |
| F ou double-clic | Ajuster l'image à la fenêtre |
| 1 | Zoom à 100 % |
| V | Retourner l'image verticalement |
| Molette | Zoomer / dézoomer |
| Ctrl+O | Ajouter des fichiers FITS, SER ou CR2 |
| Ctrl+D | Ajouter un dossier |
| Ctrl+M | Déplacer les fichiers rejetés dans « rejetes » |
| Ctrl+E | Créer un SER avec les images sélectionnées |
| Ctrl+Maj+E | Créer un SER avec toutes les images gardées |
| Ctrl+Q | Quitter |

Les touches de tri et de navigation agissent sur la liste de gauche. Si elles semblent sans effet, cliquez une fois sur un nom de fichier pour redonner la main à la liste.

## Questions fréquentes

**Le programme ne se lance pas et parle de « ModuleNotFoundError ».**
Une dépendance manque. Relancez la commande d'installation de votre distribution (section Installation), puis réessayez.

**Mon dossier est déposé mais la liste reste vide.**
Vérifiez que les fichiers ont bien une extension `.fit`, `.fits`, `.fts`, `.ser` ou `.cr2`, et qu'ils sont directement dans le dossier déposé, pas dans un sous-dossier. Le message « Aucune nouvelle image trouvée » s'affiche alors en bas de la fenêtre.

**Une image affiche « Lecture impossible ».**
Le fichier est probablement incomplet, par exemple si l'acquisition a été interrompue pendant l'enregistrement. Le message exact apparaît dans la colonne de droite. Rejetez simplement ce fichier.

**Mes images couleur restent en noir et blanc malgré la case Débayeriser.**
L'en-tête ne contient pas de mot-clé BAYERPAT. Vérifiez dans la colonne de droite ; certains logiciels d'acquisition ne l'écrivent que si l'option correspondante est activée.

**Toutes mes images se ressemblent alors que la nuit a été nuageuse.**
L'auto-stretch compense les variations de fond. Passez en « Percentiles » ou comparez la médiane affichée en bas : une image voilée a une médiane nettement plus élevée que ses voisines.

**Où sont passées les images rejetées ?**
Dans un sous-dossier `rejetes`, à côté des images d'origine. Si deux fichiers portent le même nom, le second reçoit un suffixe `_1`, `_2`… : rien n'est écrasé.

**Siril affiche mon SER à l'envers.**
Le SER reprend les lignes dans l'ordre exact où elles sont stockées dans les FITS, pour ne pas fausser la matrice de Bayer. Le sens n'a aucune incidence sur l'alignement et l'empilement ; retournez l'image finale si besoin.

**La création du SER s'arrête sur « dimensions différentes ».**
Une des images n'a pas la même taille que la première, souvent à cause d'un binning ou d'un recadrage différent. Retirez-la de la sélection ou rejetez-la, puis relancez.

**Un SER apparaît en noir et blanc avec une trame en damier.**
C'est une vidéo de caméra couleur enregistrée en brut. Cochez **Débayeriser (couleur)** ; si rien ne change, la vidéo ne précise pas sa matrice de Bayer.

**L'ajout d'un très long SER prend quelques secondes.**
Chaque image devient une ligne de la liste : une vidéo de 20 000 images donne 20 000 lignes. Les images ne sont lues qu'au moment de les afficher, la mémoire n'est donc pas un problème.

**Un CR2 affiche « installez le programme dcraw ».**
fitsview a besoin de dcraw ou du module rawpy pour décoder les RAW. Installez dcraw avec la commande de votre distribution (section Installation), puis rouvrez le fichier.

**Un CR2 affiche « Lecture impossible » alors que dcraw est installé.**
Le boîtier est peut-être plus récent que votre version de dcraw. Installez rawpy (`pip install --user --break-system-packages rawpy`), qui suit mieux les nouveaux modèles. Les CR3 des boîtiers Canon récents ne sont pas encore pris en charge.
