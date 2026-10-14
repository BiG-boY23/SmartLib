"""Shared language and catalog handling when the model is unavailable."""
import re


def response_language(question, history=(), language="auto"):
    aliases = {"bisaya": "cebuano", "filipino": "tagalog"}
    requested = aliases.get(language.casefold(), language.casefold())
    if requested != "auto":
        return requested
    for text in [question] + [turn.get("content", "") for turn in reversed(history) if turn.get("role") == "user"]:
        lower = text.casefold()
        for name in ("cebuano", "bisaya", "tagalog", "filipino", "waray", "english"):
            if re.search(r"\b" + name + r"\b", lower):
                return aliases.get(name, name)
        if re.search(r"\b(maayong|maaung|maau nga|maung|maayung|gabi[e]?|nakog|nako|unsa|ug|pangita|salamat kaayo)\b", lower):
            return "cebuano"
        if re.search(r"\b(maupay|gab i|gab-i|hain|karuyag|hin|waray)\b", lower):
            return "waray"
        if re.search(r"\b(kumusta|magandang|kailangan|libro|aklat|po|salamat|hanap)\b", lower):
            return "tagalog"
    return "english"


def catalog_terms(question):
    ignore = set("find search book books about recommend recommendation please show want need have what where which the for with a an i me can you good morning evening hello hi hey para og ug mga ko nako nakog akong ng ang sa nang libro aklat kailangan pangita hanap".split())
    words = re.findall(r"[^\W_]+", question.casefold())
    terms = [word for word in words if len(word) >= 2 and word not in ignore]
    aliases = {"it": ["information technology", "computer", "programming"], "cs": ["computer science", "programming"]}
    expanded = []
    for term in terms[:5]:
        expanded.extend(aliases.get(term, [term]))
    return list(dict.fromkeys(expanded))


def conversational_reply(question, history, language, books):
    """Return only supported conversational intents; policy lookup stays in routers."""
    lower = question.casefold().strip(" .!?,")
    lang = response_language(question, history, language)
    greetings = ("hello", "hi", "hey", "good morning", "good afternoon", "good evening", "maayong", "maaung", "maung", "maayung", "maupay", "magandang", "kumusta")
    greeting = any(lower == word or lower.startswith(word + " ") for word in greetings)
    catalog = bool(re.search(r"\b(book|books|libro|aklat|title|author|catalog|recommend|it|cs)\b", lower))
    if not greeting and not catalog:
        return None
    replies = {
        "english": ("Hello! I can help you find books, check your borrowing requests, and explain library services. What would you like help with?", "Here are matching books from the catalog. Tap a title to check availability.", "I couldn't find a match in this catalog search. Which title, author, or topic would you like?"),
        "cebuano": ("Maayong adlaw! Makatabang ko sa pagpangita og libro ug sa mga serbisyo sa library. Unsa imong gipangita?", "Aniay mga libro nga nihaom sa imong gipangita. Tan-awa ang availability sa catalog.", "Wala koy nakitang nihaom sa kini nga pagpangita. Unsang titulo, awtor, o hilisgutan imong gusto?"),
        "tagalog": ("Kumusta! Matutulungan kitang maghanap ng libro at alamin ang mga serbisyo ng library. Ano ang kailangan mo?", "Narito ang mga librong tumutugma sa hinahanap mo. Tingnan ang availability sa catalog.", "Wala akong nakitang tugma sa paghahanap na ito. Anong pamagat, awtor, o paksa ang gusto mo?"),
        "waray": ("Maupay nga adlaw! Makakabulig ako ha pagpamiling hin libro ngan ha mga serbisyo han library. Ano an imo kinahanglan?", "Adi an mga libro nga angay ha imo ginbibiling. Kitaa an availability ha catalog.", "Waray ako nakita nga tugma hini nga pagpamiling. Ano nga titulo, awtor, o paksa an imo karuyag?"),
    }
    greeting_text, found, missing = replies.get(lang, replies["english"])
    if greeting:
        if "gab" in lower and lang == "cebuano":
            greeting_text = greeting_text.replace("Maayong adlaw!", "Maayong gabii!")
        return greeting_text
    return found if books else missing
