"""
Ajoute des membres de clan a la liste d'adresses suivies.

Deux facons de s'en servir :

  python add_clan.py --clan Dabal
      Tente de lire la composition du clan via FomoScan. Ne marche que si
      l'API expose les membres (aujourd'hui elle ne le fait pas) et si la
      cle a encore du quota.

  python add_clan.py --group Dabal --handles @alice @bob @carol
      Chemin fiable : tu donnes les @ des membres, chaque handle est resolu
      en wallet verifie via /v2/user/handle/{handle} — la route qui marche.
      Les adresses sont ajoutees en "[Dabal] pseudo" dans followed_wallets.txt.

  python add_clan.py --quota
      Etat de la cle (unites restantes, periode).
"""
import argparse
import sys

sys.stdout.reconfigure(line_buffering=True)

import os

from mmscanner import fomoscan, followed


def _read_handles(path):
    """Un handle par ligne ; # commente, @ optionnel."""
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip().lstrip("@")
            if line:
                out.append(line)
    return out


def _import_remplir(path="clans/A_REMPLIR.txt"):
    """
    Lit le fichier a completer : sections [Clan] puis "handle = adresse".
    Les lignes sans adresse sont ignorees, on peut donc remplir au fur et a
    mesure et relancer autant de fois qu'on veut.
    """
    import re
    from mmscanner import followed as fmod
    ADDR = re.compile(r"([1-9A-HJ-NP-Za-km-z]{32,44}|0x[a-fA-F0-9]{40})")

    try:
        raw = open(path, "r", encoding="utf-8").read()
    except FileNotFoundError:
        print(f"{path} introuvable.")
        return 1

    group, rows, vides = "Clan", [], 0
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sec = re.match(r"^\[([^\]]+)\]$", line)
        if sec:
            group = sec.group(1)
            continue
        if "=" not in line:
            continue
        handle, _, val = line.partition("=")
        m = ADDR.search(val)
        if not m:
            vides += 1
            continue
        rows.append((m.group(1), f"[{group}] {handle.strip()}"))

    if not rows:
        print(f"Aucune adresse saisie dans {path} ({vides} lignes encore vides).")
        return 1
    n = fmod.append_followed(rows)
    print(f"{len(rows)} adresses lues, {n} ajoutees a followed_wallets.txt")
    print(f"{vides} lignes encore vides.")
    return 0


def _import_file(path, group):
    """Resout un roster et l'ajoute a followed_wallets.txt. Retourne le nb ajoute."""
    handles = _read_handles(path)
    rows, manques = [], []
    for r in fomoscan.resolve_many(handles, log=print):
        if r.get("wallet"):
            rows.append((r["wallet"], f"[{group}] {r.get('handle','')}"))
        else:
            manques.append(r.get("handle", "?"))
    n = followed.append_followed(rows) if rows else 0
    print(f"  {len(rows)}/{len(handles)} resolus, {n} ajoutes")
    if manques:
        print(f"  sans wallet verifie : {', '.join(manques[:15])}")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clan", help="nom du clan a lire via l'API")
    ap.add_argument("--group", help="etiquette de groupe, ex: Dabal")
    ap.add_argument("--handles", nargs="*", default=[], help="@handles des membres")
    ap.add_argument("--quota", action="store_true", help="etat de la cle API")
    ap.add_argument("--file", help="fichier de handles (un par ligne, # = commentaire)")
    ap.add_argument("--all", action="store_true",
                    help="traite tous les rosters du dossier clans/")
    ap.add_argument("--remplir", action="store_true",
                    help="importe les adresses saisies dans clans/A_REMPLIR.txt")
    a = ap.parse_args()

    if not fomoscan.has_key():
        print("Aucune cle FomoScan (FOMOSCAN_API_KEY dans .env).")
        return 1

    if a.quota:
        q = fomoscan.quota()
        print(f"plan {q['plan']} · periode {q['periode']} · "
              f"{q['restant']} unites restantes (utilise {q['utilise']})")
        if not q["ok"]:
            print("Quota epuise — il se recharge au changement de periode.")
        return 0

    if a.remplir:
        return _import_remplir()

    # --all : chaque fichier de clans/ est un clan, le nom du fichier est le groupe
    if a.all:
        import glob, os
        total = 0
        for path in sorted(glob.glob("clans/*.txt")):
            grp = os.path.splitext(os.path.basename(path))[0]
            print(f"=== {grp} ===")
            total += _import_file(path, grp)
        print(f"{chr(10)}Total : {total} wallets ajoutes")
        return 0

    handles = [h.lstrip("@") for h in a.handles]
    group = a.group or a.clan or "Clan"

    if a.file:
        handles += _read_handles(a.file)
        group = a.group or os.path.splitext(os.path.basename(a.file))[0]

    if a.clan and not handles:
        q = fomoscan.quota()
        if not q["ok"]:
            print(f"Quota epuise (periode {q['periode']}). Reessaie a la periode suivante,")
            print("ou donne les @ des membres :  --group X --handles @a @b @c")
            return 1
        members = fomoscan.clan_members(a.clan)
        if not members:
            print(f"L'API ne renvoie aucune composition pour le clan '{a.clan}'.")
            print("Elle n'expose pas les membres — passe par --handles.")
            return 1
        handles = [m["handle"] for m in members if m["handle"]]
        print(f"{len(handles)} membres lus depuis l'API")

    if not handles:
        print("Rien a ajouter. Donne --handles @a @b @c")
        return 1

    print(f"Resolution de {len(handles)} handles...")
    rows, manques = [], []
    for r in fomoscan.resolve_many(handles, log=print):
        w = r.get("wallet")
        if w:
            rows.append((w, f"[{group}] {r.get('handle','')}"))
        else:
            manques.append(r.get("handle", "?"))

    n = followed.append_followed(rows) if rows else 0
    print()
    print(f"{len(rows)} wallets resolus, {n} ajoutes a followed_wallets.txt")
    if manques:
        print(f"{len(manques)} sans wallet verifie sur FOMO : {', '.join(manques[:12])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
