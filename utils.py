import json
import os

class I18n:
    def __init__(self):
        self.locales = {}
        self.load_locales()

    def load_locales(self):
        pk = os.path.join(os.path.dirname(__file__), "locales")
        for filename in os.listdir(pk):
            if filename.endswith(".json"):
                lang_code = filename.split(".")[0]
                with open(os.path.join(pk, filename), "r", encoding="utf-8") as f:
                    self.locales[lang_code] = json.load(f)

    def get(self, key, lang="uz", **kwargs):
        text = self.locales.get(lang, {}).get(key, key)
        if kwargs:
            try:
                text = text.format(**kwargs)
            except Exception:
                pass 
        return text

def format_amount(val):
    try:
        # Format with spaces: 200000 -> 200 000
        return "{:,}".format(val).replace(",", " ")
    except:
        return str(val)

i18n = I18n()
