"""
Genere l'icone de MSCAN : une loupe sur des chandeliers, or sur carre noir.

Concu pour rester lisible en tres petit (barre des taches, 16-32 px) :
traits epais, peu d'elements, fort contraste. Pas d'encadre.

Usage :  python make_icon.py            -> or, l'icone de l'application
         python make_icon.py --orange   -> orange, pour le canal des lignes
Sortie :  mscan.ico + mscan.png, ou mscan-lignes.png
"""
import math
from PIL import Image, ImageDraw

GOLD = (212, 175, 55)
GOLD_L = (245, 215, 120)
# le meme dessin en orange : c'est la couleur de tout ce qui vient des traces
# de l'utilisateur, dans l'app comme sur Telegram
ORANGE = (214, 62, 12)      # les bougies : orange brule, tres proche du rouge
ORANGE_L = (255, 94, 20)     # la loupe : le meme, plus vif, pour ressortir
BLACK = (0, 0, 0)
S = 1024  # rendu haute definition, reduit ensuite


def draw(clair=None, fonce=None) -> Image.Image:
    GOLD, GOLD_L = (fonce or globals()["GOLD"]), (clair or globals()["GOLD_L"])
    img = Image.new("RGB", (S, S), BLACK)
    d = ImageDraw.Draw(img, "RGBA")

    # chandeliers en arriere-plan (le "marche" qu'on scanne)
    bougies = [(0.29, 0.55, 0.79), (0.50, 0.36, 0.67), (0.71, 0.23, 0.57)]
    for fx, top, bot in bougies:
        x = S * fx
        d.line([x, S * (top - 0.085), x, S * (bot + 0.075)], fill=GOLD + (125,), width=15)
        d.rounded_rectangle([x - S * 0.058, S * top, x + S * 0.058, S * bot],
                            radius=S * 0.019, fill=GOLD + (170,))

    # loupe : verre assombri pour detacher le cercle des bougies
    cx, cy, r = S * 0.44, S * 0.43, S * 0.263
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(0, 0, 0, 170))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=GOLD_L, width=36)

    # manche, dans l'axe du cercle
    a = math.radians(-45)
    d.line([cx + (r - 10) * math.cos(a), cy - (r - 10) * math.sin(a),
            cx + (r + S * 0.215) * math.cos(a), cy - (r + S * 0.215) * math.sin(a)],
           fill=GOLD_L, width=42)
    # bout arrondi du manche
    ex, ey = cx + (r + S * 0.215) * math.cos(a), cy - (r + S * 0.215) * math.sin(a)
    d.ellipse([ex - 21, ey - 21, ex + 21, ey + 21], fill=GOLD_L)
    return img


if __name__ == "__main__":
    import sys
    if "--orange" in sys.argv:
        img = draw(clair=ORANGE_L, fonce=ORANGE)
        img.resize((512, 512), Image.LANCZOS).save("mscan-lignes.png")
        print("mscan-lignes.png genere (512 px, pour la photo du canal Telegram)")
    else:
        img = draw()
        tailles = [16, 24, 32, 48, 64, 128, 256]
        versions = [img.resize((t, t), Image.LANCZOS) for t in tailles]
        versions[-1].save("mscan.ico", format="ICO", sizes=[(t, t) for t in tailles])
        versions[-1].save("mscan.png")
        print("mscan.ico + mscan.png generes")
