"""
Empaquette les donnees privees (listes de wallets, rosters de clans) en un
seul blob base64, destine a un secret GitHub.

Le depot peut ainsi etre public — minutes GitHub Actions illimitees — sans
qu'aucune adresse suivie n'y apparaisse. Le workflow depaquette au debut de
chaque scan, en memoire de la machine ephemere, et rien n'est jamais commite.
"""
import base64
import glob
import io
import json
import os
import sys

FICHIERS = ["followed_wallets.txt", "smart_wallets.txt", "clans_state.json"]


def _liste():
    out = list(FICHIERS) + sorted(glob.glob("clans/*.txt"))
    return [f for f in out if os.path.isfile(f)]


def pack() -> str:
    paquet = {}
    for f in _liste():
        paquet[f.replace("\\", "/")] = io.open(f, encoding="utf-8").read()
    brut = json.dumps(paquet, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(brut).decode()


def unpack(blob: str) -> int:
    paquet = json.loads(base64.b64decode(blob).decode("utf-8"))
    for chemin, contenu in paquet.items():
        dossier = os.path.dirname(chemin)
        if dossier:
            os.makedirs(dossier, exist_ok=True)
        io.open(chemin, "w", encoding="utf-8").write(contenu)
    return len(paquet)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "unpack":
        blob = os.getenv("MSCAN_DATA", "").strip()
        if not blob:
            print("MSCAN_DATA absent — le scan tournera sans wallets suivis.")
            sys.exit(0)
        print(f"{unpack(blob)} fichiers de donnees restaures")
    else:
        b = pack()
        sys.stdout.write(b)
