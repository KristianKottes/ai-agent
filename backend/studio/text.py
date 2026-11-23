"""Unicode normalization and bounded speech segmentation."""

import re
import unicodedata

ABBREVIATIONS = {
    "грн.": "гривень", "грн": "гривень", "вул.": "вулиця", "обл.": "область",
    "ім.": "імені", "тис.": "тисяч", "млн.": "мільйонів", "напр.": "наприклад",
    "т.д.": "так далі", "т. д.": "так далі", "м.": "місто",
}
MONTHS = "січня лютого березня квітня травня червня липня серпня вересня жовтня листопада грудня".split()
ONES = "нуль один два три чотири п'ять шість сім вісім дев'ять".split()
TEENS = "десять одинадцять дванадцять тринадцять чотирнадцять п'ятнадцять шістнадцять сімнадцять вісімнадцять дев'ятнадцять".split()
TENS = "— — двадцять тридцять сорок п'ятдесят шістдесят сімдесят вісімдесят дев'яносто".split()
HUNDREDS = "— сто двісті триста чотириста п'ятсот шістсот сімсот вісімсот дев'ятсот".split()
ORDINAL = "— перше друге третє четверте п’яте шосте сьоме восьме дев’яте десяте одинадцяте дванадцяте тринадцяте чотирнадцяте п’ятнадцяте шістнадцяте сімнадцяте вісімнадцяте дев’ятнадцяте двадцяте".split()
BIBLE_BOOKS = {
    "Буття": "Буття|Бут", "Вихід": "Вихід|Вих", "Левіт": "Левіт|Левит|Лев",
    "Числа": "Числа|Чисел|Чис", "Повторення Закону": "Повторення Закону|Повт|Второзаконня",
    "Ісус Навин": "Ісус Навин|Ісуса Навина|Нав", "Суддів": "Суддів|Суд", "Рут": "Рут|Руф",
    "Ездри": "Ездри|Ездр", "Неємії": "Неємії|Неемії|Неєм|Неем", "Естер": "Естер|Есфір",
    "Йова": "Йова|Йов|Іова|Іов", "Псалом": "Псалом|Псалми|Псалмів|Пс",
    "Приповісті": "Приповісті|Приповістей|Прип|Притчі|Притч|Пр",
    "Еклезіяста": "Еклезіяста|Екклезіяста|Екл|Еккл", "Пісня Пісень": "Пісня Пісень|Пісн",
    "Ісаї": "Ісаї|Ісая|Іс", "Єремії": "Єремії|Єр", "Плач Єремії": "Плач Єремії|Плач",
    "Єзекіїля": "Єзекіїля|Єзек|Єз", "Даниїла": "Даниїла|Дан",
    "Осії": "Осії|Ос", "Йоіла": "Йоіла|Йоіл|Іоїля", "Амоса": "Амоса|Ам",
    "Овдія": "Овдія|Авдія|Овд|Авд", "Йони": "Йони|Йона|Іони|Іона",
    "Михея": "Михея|Мих", "Наума": "Наума|Наум", "Авакума": "Авакума|Авак",
    "Софонії": "Софонії|Соф", "Огія": "Огія|Аггея|Ог|Аг", "Захарії": "Захарії|Зах",
    "Малахії": "Малахії|Мал", "Матвія": "Матвія|Матфея|Мат|Мт",
    "Марка": "Марка|Марк|Мк|Мр", "Луки": "Луки|Лук|Лк", "Івана": "Івана|Йоана|Іоанна|Ів|Йо",
    "Дії апостолів": "Дії апостолів|Дії|Діяння|Діян", "Римлян": "Римлян|Рим",
    "Галатів": "Галатів|Гал", "Ефесян": "Ефесян|Єфесян|Еф|Єф",
    "Филип'ян": "Филип'ян|Филип’ян|Филип|Флп", "Колосян": "Колосян|Кол",
    "Тита": "Тита|Тит", "Филимона": "Филимона|Флм", "Євреїв": "Євреїв|Євр",
    "Якова": "Якова|Як", "Юди": "Юди|Юд", "Об'явлення": "Об'явлення|Об’явлення|Одкровення|Об|Одкр",
}
for _book, _aliases, _gender in (
    ("Самуїла", "Самуїла|Сам", "f"), ("Царів", "Царів|Цар", "f"), ("Царств", "Царств", "f"),
    ("Хронік", "Хронік|Хр|Параліпоменон", "f"),
    ("Коринтян", "Коринтян|Кор", "n"), ("Солунян", "Солунян|Фессалонікійців|Сол|Фес", "n"),
    ("Тимофія", "Тимофія|Тим", "n"), ("Петра", "Петра|Пет|Пт", "n"),
    ("Івана", "Івана|Йоана|Іоанна|Ів|Йо", "n"),
):
    for _index in range(1, 5 if _book == "Царств" else 4 if _book == "Івана" else 3):
        _label = (("Перша", "Друга", "Третя", "Четверта")[_index - 1] + " книга " + _book if _gender == "f" else
                  ("Перше", "Друге", "Третє")[_index - 1] + " послання "
                  + ("до " if _book not in ("Петра", "Івана") else "") + _book)
        BIBLE_BOOKS[_label] = "|".join(str(_index) + " " + alias for alias in _aliases.split("|"))
BIBLE_ALIASES = {alias.casefold(): name for name, aliases in BIBLE_BOOKS.items() for alias in aliases.split("|")}
_BOOK_PATTERN = "|".join(re.escape(alias).replace(r"\ ", r"\s+").replace(r"\s+", r"\s*", 1)
                         if alias[0].isdigit() else re.escape(alias).replace(r"\ ", r"\s+")
                         for alias in sorted(BIBLE_ALIASES, key=len, reverse=True))
BIBLE_REFERENCE = re.compile(
    r"(?<!\w)(?P<book>" + _BOOK_PATTERN + r")\.?\s*(?P<chapter>[1-9]\d{0,2})\s*:\s*"
    r"(?P<verse>[1-9]\d{0,2})(?:\s*[-–—]\s*(?P<last>[1-9]\d{0,2}))?(?!\d)", re.I)


def ordinal(value):
    if value <= 20:
        return "третій" if value == 3 else ORDINAL[value][:-1].replace("’", "'") + "ий"
    if value < 100:
        tens, units = divmod(value, 10)
        return TENS[tens] + " " + ordinal(units) if units else {
            30: "тридцятий", 40: "сороковий", 50: "п'ятдесятий", 60: "шістдесятий",
            70: "сімдесятий", 80: "вісімдесятий", 90: "дев'яностий",
        }[value]
    if value == 100:
        return "сотий"
    return "сто " + ordinal(value - 100)


def _bible_reference(match):
    chapter, verse = int(match["chapter"]), int(match["verse"])
    last = int(match["last"]) if match["last"] else None
    if chapter > 150 or verse > 176 or (last is not None and not verse <= last <= 176):
        return match[0]
    key = re.sub(r"\s+", " ", match["book"].casefold())
    key = re.sub(r"^([1234])(?=\D)", r"\1 ", key).replace("  ", " ")
    book = BIBLE_ALIASES[key]
    passage = (f"Псалом {ordinal(chapter)}" if book == "Псалом" else f"{book}, розділ {ordinal(chapter)}")
    if last is None:
        return f"{passage}, вірш {ordinal(verse)}"
    start = ordinal(verse)
    start = start[:-2] + "ього" if start.endswith("тій") else start[:-2] + "ого"
    end = ordinal(last)
    end = end[:-2] + "ього" if end.endswith("тій") else end[:-2] + "ого"
    return f"{passage}, вірші від {start} до {end}"


def plural(value, forms):
    return forms[0 if value % 10 == 1 and value % 100 != 11 else
                 1 if 2 <= value % 10 <= 4 and not 12 <= value % 100 <= 14 else 2]


def number(value, feminine=False):
    if value < 0:
        return "мінус " + number(-value, feminine)
    for base, forms in [(10**9, ("мільярд", "мільярди", "мільярдів")),
                        (10**6, ("мільйон", "мільйони", "мільйонів")),
                        (1000, ("тисяча", "тисячі", "тисяч"))]:
        if value >= base:
            head, tail = divmod(value, base)
            return " ".join(filter(None, (number(head, base == 1000), plural(head, forms),
                                          number(tail, feminine) if tail else "")))
    parts = [HUNDREDS[value // 100]] if value >= 100 else []
    rest = value % 100
    if 10 <= rest < 20:
        parts.append(TEENS[rest - 10])
    else:
        if rest >= 20:
            parts.append(TENS[rest // 10])
        unit = rest % 10
        if unit or not parts:
            parts.append({1: "одна", 2: "дві"}.get(unit, ONES[unit]) if feminine else ONES[unit])
    return " ".join(parts)


def day(value):
    if value <= 20:
        return ORDINAL[value]
    base, unit = divmod(value, 10)
    if unit == 0:
        return "тридцяте"
    return TENS[base] + " " + ORDINAL[unit]


def _time(match):
    hour, minute = int(match[1]), int(match[2])
    if hour >= 24:
        return match[0]
    if hour == 0:
        ordinal = "нульовій"
    elif hour == 3:
        ordinal = "третій"
    elif hour >= 21:
        ordinal = "двадцять " + ORDINAL[hour-20][:-1] + "ій"
    else:
        ordinal = ORDINAL[hour][:-1] + "ій"
    if hour == 23:
        ordinal = "двадцять третій"
    ending = number(minute, True) + " " + plural(minute, ("хвилина", "хвилини", "хвилин"))
    return ordinal + " годині" + (f" {ending}" if minute else "")


def _day_genitive(value):
    phrase = day(value)
    return phrase[:-1] + ("ього" if phrase.endswith("третє") else "ого")


def _year(value):
    if 2000 <= value < 2100:
        rest = value - 2000
        if not rest:
            return "двохтисячного"
        if rest <= 31:
            return "дві тисячі " + _day_genitive(rest)
        decimal, unit = divmod(rest, 10)
        ordinal = {3: "тридцятого", 4: "сорокового", 5: "п'ятдесятого", 6: "шістдесятого",
                   7: "сімдесятого", 8: "вісімдесятого", 9: "дев'яностого"}
        return "дві тисячі " + (f"{TENS[decimal]} {_day_genitive(unit)}" if unit else ordinal[decimal])
    return number(value)


def _currency(value, symbol):
    forms = {
        "₴": ("гривня", "гривні", "гривень"),
        "$": ("долар", "долари", "доларів"),
        "€": ("євро", "євро", "євро"),
    }
    amount = int(value)
    return number(amount, symbol == "₴") + " " + plural(amount, forms[symbol])


def normalize(text, language, overrides=None):
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(str.maketrans({"’": "'", "ʼ": "'", "\u00a0": " ", "\u202f": " "}))
    if language == "uk":
        text = BIBLE_REFERENCE.sub(_bible_reference, text)
        text = re.sub(r"([$€₴])[ \t]*(-?\d{1,12})(?!\d|[,.]\d)", lambda m: _currency(m[2], m[1]), text)
        text = re.sub(r"(?<![\w.,])(-?\d{1,12})[ \t]*(₴|€|\$|грн\b)",
                      lambda m: _currency(m[1], "₴" if m[2] == "грн" else m[2]), text)
        for abbreviation, expansion in ABBREVIATIONS.items():
            text = re.sub(r"(?<!\w)" + re.escape(abbreviation) + r"(?!\w)", expansion, text, flags=re.I)
        text = re.sub(r"(?<!\d)([0-2]?\d):([0-5]\d)(?!\d)", _time, text)
        text = re.sub(r"(?<!\d)(0?[1-9]|[12]\d|3[01])[./](0?[1-9]|1[0-2])[./](\d{4})(?!\d)",
                      lambda m: f"{_day_genitive(int(m[1]))} {MONTHS[int(m[2])-1]} {_year(int(m[3]))} року", text)
        text = re.sub(r"(?<!\d)(0?[1-9]|[12]\d|3[01]) (" + "|".join(MONTHS) + r")\b",
                      lambda m: f"{_day_genitive(int(m[1]))} {m[2]}", text, flags=re.I)
        text = re.sub(r"(?<![\w\d])(-?\d{1,12})(?:([.,])(\d{1,6}))?(?!\d)",
                      lambda m: number(int(m[1])) + (" кома " + " ".join(ONES[int(n)] for n in m[3]) if m[3] else ""),
                      text)
    if overrides:
        names = sorted(overrides, key=len, reverse=True)
        mapping = {name.casefold(): value for name, value in overrides.items()}
        text = re.sub(r"(?<!\w)(?:" + "|".join(map(re.escape, names)) + r")(?!\w)",
                      lambda m: mapping[m[0].casefold()], text, flags=re.I)
    return re.sub(r"[ \t]+", " ", text).strip()


def sections(text, limit=220, *, split_sentences=True):
    """Yield text and whether the following pause ends a paragraph."""
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = " ".join(paragraph.split())
        if split_sentences:
            # Keep periods in abbreviations and initials inside their sentence.
            protected = re.sub(
                r"(?<!\w)(?:[A-ZА-ЯІЇЄҐ]|Mr|Mrs|Ms|Dr|Prof|etc|e\.g|i\.e|ім|вул|р|ст|тис|млн|обл|"
                + _BOOK_PATTERN + r")\.(?=\s)",
                lambda m: m[0].replace(".", "\u0001"), paragraph, flags=re.I,
            )
            parts = [match[0].replace("\u0001", ".").strip()
                     for match in re.finditer(r'.+?(?:[.!?…]+[»”"\']*(?=\s|$)|$)', protected)]
        else:
            parts = [paragraph]
        for index, words in enumerate(parts):
            while len(words) > limit:
                boundary = words.rfind(" ", 0, limit + 1)
                boundary = boundary if boundary > 0 else limit
                yield words[:boundary], False
                words = words[boundary:].lstrip()
            if words:
                yield words, index == len(parts) - 1


def mixed_language(text):
    return bool(re.search(r"[A-Za-z]", text) and re.search(r"[\u0400-\u04ff]", text))
