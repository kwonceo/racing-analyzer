import glob, os
BASE = "."


def _bad_hook_probe(rk):
    slug = rk.replace(" ", "_")
    pat = "*_%s.json" % slug
    return sorted(glob.glob(os.path.join(BASE, "data", "odds_history", pat)))
