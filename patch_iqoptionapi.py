"""
Patches a bug in iqoptionapi stable_api.py where __get_digital_open
crashes with TypeError when get_digital_underlying_list_data() returns None.

Run automatically via: make install
"""
import sys
import glob

SEARCH = '        digital_data = self.get_digital_underlying_list_data()["underlying"]'
REPLACE = (
    '        raw = self.get_digital_underlying_list_data()\n'
    '        if raw is None:\n'
    '            return\n'
    '        digital_data = raw.get("underlying", [])\n'
    '        if not digital_data:\n'
    '            return'
)

SEARCH2 = "                    print('Asset {} not found on consts'.format(ACTIVES))"
REPLACE2 = "                    logging.debug('Asset {} not found on consts'.format(ACTIVES))"

def find_stable_api() -> str:
    patterns = [
        ".venv/lib/python*/site-packages/iqoptionapi/stable_api.py",
        "venv/lib/python*/site-packages/iqoptionapi/stable_api.py",
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return ""


def main():
    path = find_stable_api()
    if not path:
        print("patch_iqoptionapi: stable_api.py not found — skipping")
        return

    with open(path, "r") as f:
        content = f.read()

    if SEARCH not in content:
        print(f"patch_iqoptionapi: already patched or not needed — skipping ({path})")
        return

    patched = content.replace(SEARCH, REPLACE)
    patched = patched.replace(SEARCH2, REPLACE2)

    # Ensure logging is imported in the file
    if "import logging" not in patched:
        patched = "import logging\n" + patched
    with open(path, "w") as f:
        f.write(patched)

    print(f"patch_iqoptionapi: patched {path}")


if __name__ == "__main__":
    main()
