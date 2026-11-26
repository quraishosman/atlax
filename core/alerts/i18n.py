"""Subscriber-facing Telegram translations for ATLAX."""

from __future__ import annotations

import re
from typing import Any

SUPPORTED_LANGUAGES = ("en", "sw")
LANGUAGE_LABELS = {
    "en": "🇬🇧 English",
    "sw": "🇹🇿 Kiswahili",
}

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "welcome_title": "👋 <b>Welcome to ATLAX, {name}</b>",
        "welcome_intro": (
            "ATLAX is a deterministic, multi-timeframe trading operating system. "
            "It evaluates configured markets with strict, documented, rule-based "
            "engines and routes qualifying opportunities with a clear explanation."
        ),
        "welcome_signal": (
            "📡 Every signal is structured around its pair, timeframe, direction, "
            "confidence, quality context, price levels, and current status, so you "
            "can understand what was detected and why it was sent."
        ),
        "welcome_profile": (
            "🎯 Choose Scalper, Day Trader, Swing Trader, or All Styles, then select "
            "the pairs and timeframes you want to follow. Confidence and quality "
            "preferences help shape your personal alert route."
        ),
        "welcome_tools": (
            "📊 Subscribers can review the Signal Terminal, Signal Passport and "
            "history, daily and weekly reports, market context, heatmap, correlation, "
            "and ATLAX AI for trading-related questions."
        ),
        "welcome_advisory": (
            "🛡️ ATLAX keeps its analysis explainable and advisory. It does not "
            "control a subscriber's broker account, wallet, balance, positions, or "
            "profit and loss."
        ),
        "plans": "💳 <b>Plans:</b> Standard ${standard_price} USD or Premium ${premium_price} USD for {duration_days} days",
        "payment_after_plan": "Choose your preferred payment method from the subscription menu and receive your signals after payment verification.",
        "invite_access": "🔐 Access is available by approved invitation or authorized activation.",
        "choose_action": "Choose an action below to explore ATLAX.",
        "subscribe": "💳 Subscribe",
        "about": "🧭 How ATLAX Works",
        "faq": "📚 Read FAQ",
        "help": "🆘 Help & Support",
        "language": "🌐 Language",
        "main_title": "📡 <b>ATLAX Signal Terminal</b>",
        "welcome_back": "👋 <b>Welcome back, {name}</b>",
        "payment_method": "💳 Payment method:",
        "style": "Style:",
        "timeframes": "Timeframes:",
        "symbols": "Symbols:",
        "expires": "Expires:",
        "advisory_short": "Your signals remain explainable, profile-aware, and advisory.",
        "signal_terminal": "📡 Signal Terminal",
        "profile": "👤 My Profile",
        "passport": "📚 Passport",
        "weekly": "📊 Weekly",
        "subscription": "💳 Subscription",
        "market_context": "🌍 Market Context",
        "ask_ai": "🤖 Ask ATLAX AI",
        "preferences": "⚙️ Preferences",
        "help_short": "❓ Help",
        "settings_title": "⚙️ <b>My Preferences</b>",
        "trading_style": "🏃 Trading Style",
        "pair_filter": "📈 Pair Filter",
        "min_confidence": "💯 Minimum Confidence",
        "notifications": "🔔 Notifications",
        "key_level_filter": "🏛️ Key-Level Filter",
        "quality_filter": "💎 Quality Filter",
        "premium_confluence": "✨ Premium Confluence",
        "back": "↩️ Back",
        "home": "🏠 Home",
        "language_title": "🌐 <b>Language</b>",
        "language_prompt": "Choose the language for your ATLAX Telegram experience.",
        "language_saved": "✅ Language updated.",
        "language_saved_prompt": "Your ATLAX menus will now use {selected_language} where translations are available.",
        "not_registered": "⚠️ You are not registered.\n\nUse /start to get access.",
    },
    "sw": {
        "welcome_title": "👋 <b>Karibu ATLAX, {name}</b>",
        "welcome_intro": (
            "ATLAX ni mfumo wa uendeshaji wa biashara unaotumia uchambuzi wa "
            "muda mbalimbali. Hutathmini masoko yaliyowekwa kwa kanuni kali, "
            "zilizoandikwa na zinazoeleweka, kisha hutuma fursa zinazokidhi vigezo."
        ),
        "welcome_signal": (
            "📡 Kila ishara ina jozi, muda, mwelekeo, alama ya kujiamini, ubora, "
            "viwango vya bei na hali yake, ili uelewe kilichogunduliwa na kwa nini "
            "imetumwa."
        ),
        "welcome_profile": (
            "🎯 Chagua Scalper, Day Trader, Swing Trader au Mitindo Yote, kisha "
            "chagua jozi na vipindi unavyotaka kufuatilia. Mapendeleo ya kujiamini "
            "na ubora huunda njia yako binafsi ya kupokea arifa."
        ),
        "welcome_tools": (
            "📊 Wasajili wanaweza kuangalia Signal Terminal, Signal Passport na "
            "historia, ripoti za kila siku na wiki, muktadha wa soko, heatmap, "
            "correlation na ATLAX AI kwa maswali yanayohusiana na trading."
        ),
        "welcome_advisory": (
            "🛡️ ATLAX hutoa uchambuzi unaoelezeka na wa ushauri. Haitawali akaunti "
            "ya broker, pochi, salio, nafasi au faida na hasara ya msajili."
        ),
        "plans": "💳 <b>Vifurushi:</b> Standard ${standard_price} USD au Premium ${premium_price} USD kwa siku {duration_days}",
        "payment_after_plan": "Chagua njia ya malipo kwenye menyu ya usajili na upokee ishara zako baada ya malipo kuthibitishwa.",
        "invite_access": "🔐 Ufikiaji unapatikana kwa mwaliko ulioidhinishwa au uanzishaji ulioidhinishwa.",
        "choose_action": "Chagua hatua hapa chini ili kujua zaidi kuhusu ATLAX.",
        "subscribe": "💳 Jisajili",
        "about": "🧭 Jinsi ATLAX Inavyofanya Kazi",
        "faq": "📚 Soma Maswali",
        "help": "🆘 Msaada na Huduma",
        "language": "🌐 Lugha",
        "main_title": "📡 <b>ATLAX Signal Terminal</b>",
        "welcome_back": "👋 <b>Karibu tena, {name}</b>",
        "payment_method": "💳 Njia ya malipo:",
        "style": "Mtindo:",
        "timeframes": "Vipindi:",
        "symbols": "Jozi:",
        "expires": "Inaisha:",
        "advisory_short": "Ishara zako zinaelezeka, zinazingatia wasifu wako na ni za ushauri.",
        "signal_terminal": "📡 Signal Terminal",
        "profile": "👤 Wasifu Wangu",
        "passport": "📚 Passport",
        "weekly": "📊 Wiki",
        "subscription": "💳 Usajili",
        "market_context": "🌍 Muktadha wa Soko",
        "ask_ai": "🤖 Muulize ATLAX AI",
        "preferences": "⚙️ Mapendeleo",
        "help_short": "❓ Msaada",
        "settings_title": "⚙️ <b>Mapendeleo Yangu</b>",
        "trading_style": "🏃 Mtindo wa Trading",
        "pair_filter": "📈 Chujio la Jozi",
        "min_confidence": "💯 Kujiamini kwa Chini",
        "notifications": "🔔 Arifa",
        "key_level_filter": "🏛️ Chujio la Viwango Muhimu",
        "quality_filter": "💎 Chujio la Ubora",
        "premium_confluence": "✨ Premium Confluence",
        "back": "↩️ Rudi",
        "home": "🏠 Mwanzo",
        "language_title": "🌐 <b>Lugha</b>",
        "language_prompt": "Chagua lugha ya matumizi yako ya ATLAX kwenye Telegram.",
        "language_saved": "✅ Lugha imesasishwa.",
        "language_saved_prompt": "Menyu za ATLAX zitatumia {selected_language} pale ambapo tafsiri zinapatikana.",
        "not_registered": "⚠️ Hujasajiliwa.\n\nTumia /start ili kupata ufikiaji.",
    },
}


def normalize_language(value: Any) -> str:
    """Return a supported language code, defaulting safely to English."""
    code = str(value or "").strip().lower().replace("_", "-")
    if code.startswith("sw"):
        return "sw"
    if code.startswith("en"):
        return "en"
    return "en"


def language_label(value: Any) -> str:
    return LANGUAGE_LABELS[normalize_language(value)]


def translate(key: str, language: Any = "en", **values: Any) -> str:
    """Translate a subscriber-facing key with an English fallback."""
    code = normalize_language(language)
    template = _TRANSLATIONS.get(code, {}).get(key) or _TRANSLATIONS["en"].get(key, key)
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template


# These replacements cover the existing subscriber screens while those
# screens are gradually migrated to named translation keys. They are kept
# here, at the delivery boundary, so payment and menu flows cannot silently
# bypass the selected language.
_SW_TEXT_REPLACEMENTS = (
    ("ATLAX Signal Subscription", "Usajili wa Ishara za ATLAX"),
    ("Choose the plan that matches how you want to follow the market. Access lasts for the configured subscription period and is activated only after payment review.", "Chagua kifurushi kinachoendana na namna unavyotaka kufuatilia soko. Ufikiaji hudumu kwa kipindi kilichowekwa na huwashwa baada ya malipo kukaguliwa."),
    ("Telegram signal delivery", "Uwasilishaji wa ishara kupitia Telegram"),
    ("Scalper, Day Trader, and Swing routing", "Uelekezaji wa Scalper, Day Trader na Swing"),
    ("Pair selection and Signal Terminal", "Uchaguzi wa jozi na Signal Terminal"),
    ("Signal Passport, history, and daily/weekly reports", "Signal Passport, historia na ripoti za kila siku/wiki"),
    ("Market context, heatmap, and correlation", "Muktadha wa soko, heatmap na correlation"),
    ("ATLAX AI: up to 10 questions per day", "ATLAX AI: hadi maswali 10 kwa siku"),
    ("Confidence filter", "Chujio la kujiamini"),
    ("Everything in Standard, plus:", "Kila kilichopo Standard, pamoja na:"),
    ("A/A+ selective signal route", "Njia maalum ya ishara za A/A+"),
    ("Premium signal label and confluence filter", "Alama ya ishara za Premium na chujio la confluence"),
    ("Advanced market analysis", "Uchambuzi wa juu wa soko"),
    ("High-Quality Only and Key-Level Only filters", "Vichujio vya Ubora wa Juu Pekee na Kiwango Muhimu Pekee"),
    ("Priority routing, premium support, and early feature access", "Uelekezaji wa kipaumbele, msaada wa Premium na matumizi ya mapema ya vipengele"),
    ("Choose a payment method below. Payment instructions appear before ATLAX creates your reference.", "Chagua njia ya malipo hapa chini. Maelekezo ya malipo yataonekana kabla ATLAX haijatengeneza rejea yako."),
    ("Choose a payment method below. ATLAX will show the destination and payment steps first.", "Chagua njia ya malipo hapa chini. ATLAX itaonyesha anwani ya kupokea na hatua za malipo kwanza."),
    ("Choose a plan below. ATLAX will show the available payment methods after your selection.", "Chagua kifurushi hapa chini. ATLAX itaonyesha njia za malipo baada ya uchaguzi wako."),
    ("Payment methods are not configured yet. Please contact the admin.", "Njia za malipo bado hazijawekwa. Tafadhali wasiliana na msimamizi."),
    ("is available.", "inapatikana."),
    ("payment details are being configured. Selecting it will explain what is missing.", "maelezo ya malipo yanawekwa. Ukichagua utaona kinachokosekana."),
    ("Checkout destinations are temporarily unavailable. Please contact support so the payment setup can be completed safely.", "Anwani za malipo hazipatikani kwa muda. Wasiliana na msaada ili usanidi wa malipo ukamilishwe kwa usalama."),
    ("after payment, submit the transaction ID using the payment reference shown by ATLAX. An administrator verifies the payment before signal access is enabled.", "baada ya malipo, tuma kitambulisho cha muamala ukitumia rejea iliyoonyeshwa na ATLAX. Msimamizi atathibitisha malipo kabla ya ishara kuanzishwa."),
    ("Current access expires:", "Ufikiaji wa sasa unaisha:"),
    ("Renewing while access is active extends your current expiry instead of replacing it.", "Kufanya upya wakati ufikiaji bado unaendelea huongeza tarehe ya mwisho iliyopo badala ya kuibadilisha."),
    ("Payment Instructions —", "Maelekezo ya Malipo —"),
    ("Payment Reference Generated", "Rejea ya Malipo Imetengenezwa"),
    ("PREMIUM SIGNAL", "ISHARA YA PREMIUM"),
    ("STANDARD SIGNAL", "ISHARA YA STANDARD"),
    ("HTF KEY-LEVEL", "KIWANGO MUHIMU CHA HTF"),
    ("OPEN-STRUCTURE", "MUUNDO WAZI"),
    ("KEY-LEVEL UNKNOWN", "KIWANGO MUHIMU HAKIJULIKANI"),
    ("SETUP DETECTED", "MIPANGILIO IMEGUNDULIWA"),
    ("TRADE TARGETS", "MALENGO YA TRADE"),
    ("Strength", "Nguvu"),
    ("ALERT 2 fires when price enters setup zone", "ARIFA YA 2 itatumwa bei inapoingia kwenye eneo la mpangilio"),
    ("Detected", "Imegunduliwa"),
    ("Choose a payment method below.", "Chagua njia ya malipo hapa chini."),
    ("Selected:", "Umechagua:"),
    ("Verification:", "Uthibitishaji:"),
    ("Use this reference when submitting your payment proof.", "Tumia rejea hii unapotuma uthibitisho wa malipo."),
    ("Reference expires in:", "Rejea inaisha baada ya:"),
    ("Your unique ATLAX payment reference is ready.", "Rejea yako ya kipekee ya malipo ya ATLAX iko tayari."),
    ("Complete the payment using the instructions below, then submit the matching proof so the ATLAX team can verify and activate your access.", "Kamilisha malipo kwa kutumia maelekezo hapa chini, kisha tuma uthibitisho unaolingana ili timu ya ATLAX ithibitishe na kuwasha ufikiaji wako."),
    ("Payment reference:", "Rejea ya malipo:"),
    ("Routing profile after activation:", "Wasifu wa uelekezaji baada ya kuwashwa:"),
    ("Send the exact asset on the exact network to:", "Tuma mali hiyo hiyo kwenye mtandao huo huo kwenda:"),
    ("Sending a different asset or using another network may permanently lose the funds.", "Kutuma mali tofauti au kutumia mtandao mwingine kunaweza kusababisha upotevu wa fedha."),
    ("Open your crypto wallet and choose", "Fungua pochi yako ya crypto na uchague"),
    ("Select the", "Chagua"),
    ("Sending on another network can permanently lose the funds.", "Kutuma kwenye mtandao mwingine kunaweza kusababisha upotevu wa fedha."),
    ("Send exactly", "Tuma kiasi sahihi cha"),
    ("worth of", "chenye thamani ya"),
    ("Confirm the asset, network, and destination address carefully before sending.", "Thibitisha mali, mtandao na anwani ya kupokea kwa makini kabla ya kutuma."),
    ("ATLAX will never ask for your wallet seed phrase or private key.", "ATLAX haitawahi kuomba maneno ya siri ya pochi yako au private key."),
    ("After the transfer is confirmed, submit the", "Baada ya uhamisho kuthibitishwa, tuma"),
    ("The payment reference is used by ATLAX to match your request. It is not a substitute for the transaction proof.", "Rejea ya malipo hutumiwa na ATLAX kulinganisha ombi lako. Haiwezi kuchukua nafasi ya uthibitisho wa muamala."),
    ("Use the mobile-money menu that matches your network. The ATLAX merchant number and amount are shown below:", "Tumia menyu ya fedha za simu inayolingana na mtandao wako. Namba ya mfanyabiashara ya ATLAX na kiasi vinaonyeshwa hapa chini:"),
    ("Enter the amount:", "Ingiza kiasi:"),
    ("Enter the current TZS equivalent of", "Ingiza thamani ya sasa ya TZS ya"),
    ("confirm today's amount with ATLAX before paying.", "thibitisha kiasi cha leo na ATLAX kabla ya kulipa."),
    ("Enter your M-Pesa PIN, confirm the merchant details, and keep the confirmation SMS.", "Ingiza PIN yako ya M-Pesa, thibitisha maelezo ya mfanyabiashara na hifadhi SMS ya uthibitisho."),
    ("Use the Halotel mobile-money menu to pay through the Vodacom M-Pesa Lipa Namba shown above.", "Tumia menyu ya fedha za simu ya Halotel kulipa kupitia Lipa Namba ya Vodacom M-Pesa iliyoonyeshwa hapo juu."),
    ("After payment, submit the M-Pesa transaction ID:", "Baada ya malipo, tuma kitambulisho cha muamala wa M-Pesa:"),
    ("Never share your M-Pesa PIN with ATLAX, support, or anyone else.", "Usishiriki PIN yako ya M-Pesa na ATLAX, msaada au mtu mwingine yeyote."),
    ("Complete payment only through the configured secure checkout page:", "Kamilisha malipo kupitia ukurasa salama wa malipo uliowekwa pekee:"),
    ("Open secure card checkout", "Fungua malipo salama ya kadi"),
    ("Never send card details, PINs, or one-time codes in Telegram.", "Usitume maelezo ya kadi, PIN au misimbo ya matumizi ya mara moja kwenye Telegram."),
    ("After payment, generate your ATLAX reference and submit the matching transaction signature.", "Baada ya malipo, tengeneza rejea yako ya ATLAX na tuma saini ya muamala inayolingana."),
    ("Choose a view to understand the market around ATLAX alerts:", "Chagua mwonekano wa kuelewa soko linalozunguka arifa za ATLAX:"),
    ("Heatmap shows where configured pairs are showing stronger or weaker pressure.", "Heatmap huonyesha mahali ambapo jozi zilizowekwa zinaonyesha msukumo mkubwa au mdogo."),
    ("Correlation shows whether two pairs have recently moved together or in opposite directions.", "Correlation huonyesha kama jozi mbili zimesogea pamoja au kinyume hivi karibuni."),
    ("Economic news shows scheduled high-impact events that may affect currencies.", "Habari za uchumi huonyesha matukio makubwa yaliyopangwa ambayo yanaweza kuathiri sarafu."),
    ("Frequently Asked Questions", "Maswali Yanayoulizwa Mara kwa Mara"),
    ("How ATLAX Works", "Jinsi ATLAX Inavyofanya Kazi"),
    ("Help & Support", "Msaada na Huduma"),
    ("Signal Passport", "Signal Passport"),
    ("Weekly Signal Report", "Ripoti ya Ishara za Wiki"),
    ("Daily Signal Report", "Ripoti ya Ishara za Kila Siku"),
    ("Signal Terminal", "Signal Terminal"),
    ("No activity recorded today.", "Hakuna shughuli iliyorekodiwa leo."),
    ("No signals are available yet.", "Hakuna ishara zinazopatikana bado."),
    ("No active access is attached to this account yet.", "Hakuna ufikiaji unaotumika uliounganishwa na akaunti hii bado."),
    ("You are not registered. Use /start.", "Hujasajiliwa. Tumia /start."),
    ("You are not registered.", "Hujasajiliwa."),
    ("Use /start to get access.", "Tumia /start ili kupata ufikiaji."),
    ("Only receive signals at or above this score:", "Pokea ishara zenye alama hii au zaidi:"),
    ("Choose what updates you receive:", "Chagua aina ya masasisho unayopokea:"),
    ("You can select up to 12 pairs.", "Unaweza kuchagua hadi jozi 12."),
    ("Language selection rejected.", "Uchaguzi wa lugha umekataliwa."),
    ("Saved: trading style updated.", "✅ Imehifadhiwa: mtindo wa trading umesasishwa."),
    ("Saved: pair filter updated.", "✅ Imehifadhiwa: chujio la jozi limesasishwa."),
    ("Saved: minimum confidence updated.", "✅ Imehifadhiwa: kiwango cha chini cha kujiamini kimesasishwa."),
    ("Saved: notification mode updated.", "✅ Imehifadhiwa: hali ya arifa imesasishwa."),
    ("Saved: you will receive", "✅ Imehifadhiwa: utapokea"),
    ("You are not authorized to review payments.", "Huna ruhusa ya kukagua malipo."),
    ("Payments are not configured.", "Malipo hayajawekwa."),
    ("Payment proof could not be verified. It was not approved.", "Uthibitisho wa malipo haukuweza kuthibitishwa. Haujaidhinishwa."),
    ("The payment remains pending.", "Malipo bado yanasubiri."),
    ("You need an active subscription first. Use /subscribe.", "Unahitaji usajili unaotumika kwanza. Tumia /subscribe."),
    ("AI advisory is not configured.", "Ushauri wa AI haujawekwa."),
    ("ATLAX is thinking…", "ATLAX inafikiria…"),
    ("AI query failed. Please try again later.", "Swali la AI limeshindwa. Tafadhali jaribu tena baadaye."),
    ("Welcome to ATLAX AI Signals", "Karibu kwenye Ishara za AI za ATLAX"),
    ("Choose a payment method below or use /buy crypto or /buy mpesa.", "Chagua njia ya malipo hapa chini au tumia /buy crypto au /buy mpesa."),
    ("After payment is confirmed, access is activated on Telegram automatically.", "Baada ya malipo kuthibitishwa, ufikiaji utawezeshwa kiotomatiki kwenye Telegram."),
    ("Choose a payment method:", "Chagua njia ya malipo:"),
    ("Choose a valid subscription plan first.", "Chagua kifurushi halali cha usajili kwanza."),
    ("is not configured yet.", "bado haijawekwa."),
    ("Plan:", "Kifurushi:"),
    ("Amount:", "Kiasi:"),
    ("Network:", "Mtandao:"),
    ("Asset:", "Mali:"),
    ("Payment method:", "Njia ya malipo:"),
    ("Reference:", "Rejea:"),
    ("Renewal will extend your access by", "Kufanya upya kutaongeza ufikiaji wako kwa"),
    ("days.", "siku."),
    ("Payment Submitted", "Malipo Yamewasilishwa"),
    ("Payment approved", "Malipo yameidhinishwa"),
    ("Payment rejected", "Malipo yamekataliwa"),
    ("Card Payment", "Malipo kwa Kadi"),
    ("Pay by Card", "Lipa kwa Kadi"),
    ("Pay with M-Pesa Lipa Namba", "Lipa kwa M-Pesa Lipa Namba"),
    ("Solana transaction signature", "Saini ya muamala wa Solana"),
    ("network transaction ID/hash", "Kitambulisho/hash ya muamala wa mtandao"),
    ("Payment request expires at:", "Ombi la malipo linaisha:"),
    ("Need help? Contact", "Unahitaji msaada? Wasiliana na"),
    ("Payment is being verified", "Malipo yanathibitishwa"),
    ("Your payment request enters verification.", "Ombi lako la malipo limeingia kwenye uthibitishaji."),
    ("access is activated", "ufikiaji unawezeshwa"),
    ("No active access", "Hakuna ufikiaji unaotumika"),
    ("No expiry set", "Hakuna tarehe ya mwisho iliyowekwa"),
    ("Payment reference not found.", "Rejea ya malipo haikupatikana."),
    ("Payment proof submitted.", "Uthibitisho wa malipo umewasilishwa."),
    ("Payment was already approved.", "Malipo yalikuwa tayari yameidhinishwa."),
    ("Payment rejected.", "Malipo yamekataliwa."),
    ("Payment confirmed and subscriber activated.", "Malipo yamethibitishwa na msajili amewezeshwa."),
    ("Please contact support.", "Tafadhali wasiliana na msaada."),
    ("No payment requests have been created yet.", "Hakuna maombi ya malipo yaliyoundwa bado."),
    # Public FAQ copy. The FAQ renderer wraps answers across lines, so these
    # entries intentionally translate each rendered line independently.
    ("WHAT IS ATLAX?", "ATLAX NI NINI?"),
    ("HOW DOES ATLAX WORK?", "ATLAX INAFANYA KAZI VIPI?"),
    ("WHAT DO SUBSCRIBERS RECEIVE?", "WASAJILI WANAPOKEA NINI?"),
    ("DOES ATLAX MANAGE MY TRADING ACCOUNT?", "JE, ATLAX INASIMAMIA AKAUNTI YANGU YA TRADING?"),
    ("DOES ATLAX PLACE TRADES FOR SUBSCRIBERS?", "JE, ATLAX INAWEKA TRADES KWA WASAJILI?"),
    ("WHICH TRADING STYLES ARE AVAILABLE?", "NI MITINDO GANI YA TRADING INAYOPATIKANA?"),
    ("CAN I CHOOSE WHICH PAIRS I RECEIVE?", "JE, NAWEZA KUCHAGUA JOZI NITAKAZOPOKEA?"),
    ("WHAT IS PREMIUM CONFLUENCE?", "PREMIUM CONFLUENCE NI NINI?"),
    ("WHAT ARE INDEPENDENT FILTERS?", "VICHUJIO HURU NI NINI?"),
    ("WHAT DOES HIGH-QUALITY ONLY MEAN?", "UBORA WA JUU PEKEE UNAMAANISHA NINI?"),
    ("WHY HAVE I NOT RECEIVED A SIGNAL?", "KWA NINI SIJAPOKEA ISHARA?"),
    ("WHAT DOES A SIGNAL MEAN?", "ISHARA INAMAANISHA NINI?"),
    ("DOES A CONFIDENCE SCORE MEAN A TRADE WILL WIN?", "JE, ALAMA YA KUJIAMINI INAMAANISHA TRADE ITASHINDA?"),
    ("HOW DO I SUBSCRIBE?", "NINAJISAJILI VIPI?"),
    ("WHICH PAYMENT METHODS ARE AVAILABLE?", "NI NJIA GANI ZA MALIPO ZINAPATIKANA?"),
    ("HOW DO I PAY WITH M-PESA?", "NALIPAJE KWA M-PESA?"),
    ("WHAT HAPPENS AFTER I SUBMIT PAYMENT PROOF?", "NINI HUTOKEA BAADA YA KUTUMA UTHIBITISHO WA MALIPO?"),
    ("CAN I RENEW BEFORE MY ACCESS EXPIRES?", "JE, NAWEZA KUFANYA UPYA KABLA UFIKIAJI WANGU KUISHA?"),
    ("CAN I USE ATLAX AI?", "JE, NAWEZA KUTUMIA ATLAX AI?"),
    ("WHAT CAN ATLAX AI NOT DO?", "ATLAX AI HAIWEZI KUFANYA NINI?"),
    ("ARE ATLAX RESULTS GUARANTEED?", "JE, MATOKEO YA ATLAX YANAHIDIWA?"),
    ("HOW DO I CHANGE MY SETTINGS?", "NINABADILISHAJE MIPANGILIO YANGU?"),
    ("HOW DO I CONTACT SUPPORT?", "NINAWASILIANEJE NA MSAADA?"),
    ("atlax is a market-intelligence service that delivers structured, explainable", "atlax ni huduma ya taarifa za soko inayotoa"),
    ("trading signals and market context through telegram. it helps subscribers", "ishara za trading na muktadha wa soko unaoelezeka kupitia telegram. huwasaidia wasajili"),
    ("follow a chosen style and review information before making their own decisions.", "kufuata mtindo waliochagua na kukagua taarifa kabla ya kufanya maamuzi yao."),
    ("atlax watches the configured markets, waits for qualifying opportunities, and", "atlax hufuatilia masoko yaliyowekwa, husubiri fursa zinazokidhi vigezo, na"),
    ("sends alerts that match your selected style, timeframes, pairs, and settings.", "hutuma arifa zinazoendana na mtindo, vipindi, jozi na mipangilio uliyochagua."),
    ("you remain responsible for reviewing every alert and deciding what to do.", "wewe bado unawajibika kukagua kila arifa na kuamua cha kufanya."),
    ("subscribers receive telegram alerts for their selected style and pairs. an", "wasajili hupokea arifa za telegram za mtindo na jozi walizochagua. arifa"),
    ("alert may include the pair, timeframe, direction, price levels, confidence,", "inaweza kuwa na jozi, kipindi, mwelekeo, viwango vya bei, kujiamini,"),
    ("status, and a short explanation when the information is available.", "hali na maelezo mafupi pale taarifa hizo zinapopatikana."),
    ("status, and a short explanation when the information inapatikana.", "hali na maelezo mafupi pale taarifa hizo zinapopatikana."),
    ("no. atlax does not connect to, control, or manage a subscriber's broker account,", "hapana. atlax haiunganishi, haitawali wala kusimamia akaunti ya broker ya msajili,"),
    ("wallet, balance, positions, profit, or loss.", "pochi, salio, nafasi, faida au hasara."),
    ("no. subscriber access is for signal delivery and market information. atlax", "hapana. ufikiaji wa msajili ni kwa kupokea ishara na taarifa za soko. atlax"),
    ("does not place, change, close, or manage trades on a subscriber's account.", "haiweki, haibadilishi, haifungi wala kusimamia trades kwenye akaunti ya msajili."),
    ("scalper receives short-term alerts. day trader receives intraday alerts. swing", "scalper hupokea arifa za muda mfupi. day trader hupokea arifa za ndani ya siku. swing"),
    ("trader receives broader market alerts. all styles receives alerts from all", "trader hupokea arifa za mtazamo mpana wa soko. mitindo yote hupokea arifa kutoka"),
    ("configured subscriber timeframes. you can change your style later in settings.", "vipindi vyote vilivyowekwa. unaweza kubadilisha mtindo wako baadaye kwenye mipangilio."),
    ("yes. open signal settings and choose the pairs you want to follow. leaving the", "ndiyo. fungua mipangilio ya ishara na uchague jozi unazotaka kufuatilia. ukiacha"),
    ("pair selection empty means you receive alerts for all available configured", "sehemu ya uchaguzi wa jozi ikiwa tupu, utapokea arifa za jozi zote zilizowekwa"),
    ("pairs.", "na zinazopatikana."),
    ("premium confluence is the strictest subscriber filter. an alert must show both", "premium confluence ni chujio kali zaidi la msajili. arifa lazima ionyeshe"),
    ("clear setup quality and an important market area visible on a broader view.", "ubora wazi wa mpangilio na eneo muhimu la soko linaloonekana kwenye mtazamo mpana."),
    ("this option is designed for subscribers who prefer fewer, more selective", "chaguo hili limeundwa kwa wasajili wanaopendelea"),
    ("alerts.", "arifa chache na zilizochaguliwa zaidi."),
    ("independent filters let you choose the quality filter and the major-price-area", "vichujio huru hukuruhusu kuchagua chujio la ubora na chujio la eneo kuu la bei"),
    ("filter separately. you can receive all valid alerts, only stronger alerts, only", "kila kimoja. unaweza kupokea arifa zote halali, arifa zenye nguvu tu, arifa za"),
    ("alerts near major price areas, or use both filters together.", "karibu na maeneo makuu ya bei, au kutumia vichujio vyote pamoja."),
    ("high-quality only means atlax applies stricter checks designed to remove weak", "ubora wa juu pekee unamaanisha atlax hutumia ukaguzi mkali zaidi kuondoa arifa dhaifu"),
    ("or noisy alerts and favor clearer opportunities with stronger evidence. it", "au zenye kelele na kupendelea fursa zilizo wazi zenye ushahidi mkubwa. hii"),
    ("does not guarantee a winning result.", "haihakikishi matokeo ya ushindi."),
    ("there may be no qualifying opportunity for your selected style or pairs, the", "huenda hakuna fursa inayokidhi vigezo kwa mtindo au jozi ulizochagua, soko"),
    ("market may be quiet or closed, your filters may be strict, or market data may", "huenda likawa tulivu au limefungwa, vichujio vyako vikawa vikali, au data ya soko"),
    ("be temporarily unavailable. atlax does not create alerts just to fill the", "isiwepo kwa muda. atlax haitengenezi arifa kwa ajili ya kujaza"),
    ("feed.", "mpasho."),
    ("a signal is an advisory market alert based on atlax's documented analysis", "ishara ni arifa ya ushauri wa soko inayotokana na mchakato wa uchambuzi wa atlax"),
    ("process. it is information for independent review, not a promise, instruction,", "uliowekwa. ni taarifa ya kukagua kwa kujitegemea, si ahadi, agizo"),
    ("or guarantee of a result.", "au dhamana ya matokeo."),
    ("no. a confidence score describes the strength of the available evidence. it is", "hapana. alama ya kujiamini inaeleza nguvu ya ushahidi uliopo. si"),
    ("not a guaranteed probability of profit and it cannot remove market risk.", "uwezekano wa faida uliohakikishwa na haiwezi kuondoa hatari ya soko."),
    ("use `/subscribe` and choose an available payment method. atlax creates a", "tumia `/subscribe` na uchague njia ya malipo inayopatikana. atlax huunda"),
    ("use <code>/subscribe</code> and choose an available payment method. atlax creates a", "tumia <code>/subscribe</code> na uchague njia ya malipo inayopatikana. atlax huunda"),
    ("payment reference for your request. after payment, submit the transaction", "rejea ya malipo kwa ombi lako. baada ya malipo, tuma rejea ya muamala"),
    ("reference with `/paid` and wait for verification before access is activated.", "pamoja na `/paid` na subiri uthibitishaji kabla ya ufikiaji kuwashwa."),
    ("reference with <code>/paid</code> and wait for verification before ufikiaji unawezeshwa.", "pamoja na <code>/paid</code> na subiri uthibitishaji kabla ya ufikiaji kuwashwa."),
    ("atlax shows the available payment methods in the subscription menu. these may", "atlax huonyesha njia za malipo zinazopatikana kwenye menyu ya usajili. zinaweza"),
    ("include usdc on the solana network and a mobile-money payment option. always", "kujumuisha usdc kwenye mtandao wa solana na chaguo la fedha za simu. fuata"),
    ("follow the payment details and reference shown in your own checkout.", "maelezo ya malipo na rejea inayoonyeshwa kwenye checkout yako."),
    ("open the m-pesa option in `/subscribe` and follow the displayed instructions.", "fungua chaguo la m-pesa kwenye `/subscribe` na fuata maelekezo yanayoonyeshwa."),
    ("open the m-pesa option in <code>/subscribe</code> and follow the displayed instructions.", "fungua chaguo la m-pesa kwenye <code>/subscribe</code> na fuata maelekezo yanayoonyeshwa."),
    ("use the atlax payment number and amount shown there, keep your confirmation", "tumia namba ya malipo ya atlax na kiasi kinachoonyeshwa hapo, hifadhi ujumbe wa uthibitisho"),
    ("message, and submit only the requested payment reference for verification.", "na tuma tu rejea ya malipo iliyoombwa kwa uthibitishaji."),
    ("your payment request enters verification. an authorized atlax administrator", "ombi lako la malipo linaingia kwenye uthibitishaji. msimamizi aliyeidhinishwa wa atlax"),
    ("checks the reference and activates access after the payment is confirmed. use", "hukagua rejea na huwasha ufikiaji baada ya malipo kuthibitishwa. tumia"),
    ("`/status` to review the latest state.", "`/status` kukagua hali ya sasa."),
    ("<code>/status</code> to review the latest state.", "<code>/status</code> kukagua hali ya sasa."),
    ("yes. use `/renew` or open the subscription menu. when access is still active,", "ndiyo. tumia `/renew` au fungua menyu ya usajili. ufikiaji ukiwa bado unatumika,"),
    ("yes. use <code>/renew</code> or open the subscription menu. when access is still active,", "ndiyo. tumia <code>/renew</code> au fungua menyu ya usajili. ufikiaji ukiwa bado unatumika,"),
    ("the renewal extends the current period instead of removing your existing time.", "kufanya upya huongeza kipindi cha sasa badala ya kuondoa muda uliopo."),
    ("active subscribers can ask atlax ai about market news, heatmap context, pair", "wasajili wanaotumika wanaweza kuuliza atlax ai kuhusu habari za soko, muktadha wa heatmap na"),
    ("relationships, signal explanations, public atlax results, and general trading", "uhusiano wa jozi, maelezo ya ishara, matokeo ya umma ya atlax na elimu ya jumla ya trading"),
    ("education. ATLAX AI is advisory and subject to a fair-use daily limit.", "elimu. ATLAX AI ni ya ushauri na ina kikomo cha matumizi ya haki kwa siku."),
    ("ATLAX AI cannot access or manage your account, place trades, reveal protected", "ATLAX AI haiwezi kufikia au kusimamia akaunti yako, kuweka trades, kufichua"),
    ("system details, guarantee an outcome, or replace your own risk decisions.", "maelezo yaliyolindwa ya mfumo, kuhakikisha matokeo au kuchukua nafasi ya maamuzi yako ya hatari."),
    ("no. markets are uncertain and past results do not guarantee future results.", "hapana. masoko hayana uhakika na matokeo ya zamani hayahakikishi matokeo ya baadaye."),
    ("atlax provides information for independent review. use a personal risk plan", "atlax hutoa taarifa kwa ajili ya ukaguzi wa kujitegemea. tumia mpango binafsi wa hatari"),
    ("and seek qualified advice when appropriate.", "na tafuta ushauri wenye sifa inapofaa."),
    ("use `/settings` to update pairs, notifications, confidence, quality filters,", "tumia `/settings` kusasisha jozi, arifa, kujiamini na vichujio vya ubora,"),
    ("use <code>/settings</code> to update pairs, notifications, confidence, quality filters,", "tumia <code>/settings</code> kusasisha jozi, arifa, kujiamini na vichujio vya ubora,"),
    ("and the premium confluence option. use `/style` to change your trading style.", "pamoja na chaguo la premium confluence. tumia `/style` kubadilisha mtindo wako wa trading."),
    ("and the premium confluence option. use <code>/style</code> to change your trading style.", "pamoja na chaguo la premium confluence. tumia <code>/style</code> kubadilisha mtindo wako wa trading."),
    ("for access, payment, or account questions, contact the atlax administrator at", "kwa maswali ya ufikiaji, malipo au akaunti, wasiliana na msimamizi wa atlax kupitia"),
    ("@ocrooxx. never send a password, payment pin, wallet seed phrase, or private", "@ocrooxx. usitume kamwe nenosiri, PIN ya malipo, maneno ya siri ya pochi au"),
    ("key in a support message.", "ufunguo wa siri kwenye ujumbe wa msaada."),
    # Signal Passport and daily/weekly report copy.
    ("Recent stored signal records:", "Rekodi za ishara zilizohifadhiwa hivi karibuni:"),
    ("Created:", "Iliyoundwa:"),
    ("Status:", "Hali:"),
    ("Outcome:", "Matokeo:"),
    ("ATR quality:", "Ubora wa ATR:"),
    ("Major key level", "Kiwango kikuu muhimu"),
    ("Open structure", "Muundo wazi"),
    ("Unavailable", "Haipatikani"),
    ("Invalidation:", "Ubatilishaji:"),
    ("Results are stored signal outcomes, not a promise of future performance.", "Matokeo haya ni ya ishara zilizohifadhiwa, si ahadi ya utendaji wa baadaye."),
    ("Performance for", "Utendaji kwa"),
    ("Signals recorded:", "Ishara zilizorekodiwa:"),
    ("Open:", "Zilizo wazi:"),
    ("Resolved:", "Zilizotatuliwa:"),
    ("Unknown:", "Zisizojulikana:"),
    ("By Pair", "Kwa Jozi"),
    ("By Trading Style", "Kwa Mtindo wa Trading"),
    ("No pair records in this period.", "Hakuna rekodi za jozi katika kipindi hiki."),
    ("No style records in this period.", "Hakuna rekodi za mitindo katika kipindi hiki."),
    ("Unknown outcomes are excluded from win-rate calculations.", "Matokeo yasiyojulikana hayajajumuishwa kwenye hesabu ya kiwango cha ushindi."),
    ("last 7 days", "siku 7 zilizopita"),
    ("today", "leo"),
    ("signals ·", "ishara ·"),
    (" wins ·", " ushindi ·"),
    (" losses ·", " hasara ·"),
    (" resolved win rate", " kiwango cha ushindi kilichotatuliwa"),
)

_SW_BUTTON_TRANSLATIONS = (
    ("Open ATLAX", "Fungua ATLAX"),
    ("Done", "Imekamilika"),
    ("Previous", "Iliyotangulia"),
    ("Next", "Ifuatayo"),
    ("Profile", "Wasifu"),
    ("Refresh", "Onyesha Upya"),
    ("Clear", "Futa"),
    ("Back to AI", "Rudi kwa AI"),
    ("Back to Home", "Rudi Mwanzo"),
    ("Back to Signal Terminal", "Rudi kwenye Signal Terminal"),
    ("Back to Risk", "Rudi kwenye Hatari"),
    ("Back to Stats", "Rudi kwenye Takwimu"),
    ("Back to System", "Rudi kwenye Mfumo"),
    ("Subscribe / Renew", "Jisajili / Sasisha"),
    ("Subscribe", "Jisajili"),
    ("Standard Plan", "Kifurushi cha Standard"),
    ("Premium Plan", "Kifurushi cha Premium"),
    ("Change Plan", "Badilisha Kifurushi"),
    ("My Access", "Ufikiaji Wangu"),
    ("Payment History", "Historia ya Malipo"),
    ("Back to Subscription", "Rudi kwenye Usajili"),
    ("Payment Methods", "Njia za Malipo"),
    ("How ATLAX Works", "Jinsi ATLAX Inavyofanya Kazi"),
    ("Read FAQ", "Soma Maswali"),
    ("Help & Support", "Msaada na Huduma"),
    ("Signal Terminal", "Signal Terminal"),
    ("My Profile", "Wasifu Wangu"),
    ("Passport", "Passport"),
    ("Weekly", "Wiki"),
    ("Subscription", "Usajili"),
    ("Market Context", "Muktadha wa Soko"),
    ("Market & News", "Soko na Habari"),
    ("Ask ATLAX AI", "Muulize ATLAX AI"),
    ("Preferences", "Mapendeleo"),
    ("Help", "Msaada"),
    ("Language", "Lugha"),
    ("Trading Style", "Mtindo wa Trading"),
    ("Symbol Filter", "Chujio la Jozi"),
    ("Pair Filter", "Chujio la Jozi"),
    ("Min Confidence", "Kujiamini kwa Chini"),
    ("Minimum Confidence", "Kujiamini kwa Chini"),
    ("Notifications", "Arifa"),
    ("Key-Level Filter", "Chujio la Viwango Muhimu"),
    ("Quality Filter", "Chujio la Ubora"),
    ("Premium Confluence", "Premium Confluence"),
    ("Key-Level Setups Only", "Viwango Muhimu Pekee"),
    ("All Quality Setups", "Mipangilio Yote ya Ubora"),
    ("High-Quality Setups Only", "Mipangilio ya Ubora wa Juu Pekee"),
    ("All Valid Setups", "Mipangilio Yote Halali"),
    ("Independent Filters", "Vichujio Huru"),
    ("All Updates", "Masasisho Yote"),
    ("Signals Only", "Ishara Pekee"),
    ("Minimal", "Kidogo"),
    ("Back to Settings", "Rudi kwenye Mapendeleo"),
    ("Back to Menu", "Rudi kwenye Menyu"),
    ("Back to Market", "Rudi kwenye Soko"),
    ("Back to Subscribers", "Rudi kwa Wasajili"),
    ("Back to Admin", "Rudi kwa Msimamizi"),
    ("Main Menu", "Menyu Kuu"),
    ("Home", "Mwanzo"),
    ("Back", "Rudi"),
    ("Cancel", "Ghairi"),
    ("Generate Payment Reference", "Tengeneza Rejea ya Malipo"),
    ("Submit Payment Proof", "Tuma Uthibitisho wa Malipo"),
    ("Check Status", "Angalia Hali"),
    ("Contact Support", "Wasiliana na Msaada"),
    ("View Signals", "Angalia Ishara"),
    ("Settings", "Mipangilio"),
    ("My Preferences", "Mapendeleo Yangu"),
    ("My Settings", "Mipangilio Yangu"),
    ("Renew Access", "Sasisha Ufikiaji"),
    ("Pay with Solana", "Lipa kwa Solana"),
    ("Pay with M-Pesa", "Lipa kwa M-Pesa"),
    ("Card Payment", "Malipo kwa Kadi"),
    ("Economic News", "Habari za Uchumi"),
    ("Correlations", "Correlation"),
    ("Correlation", "Uhusiano"),
    ("Best Pair", "Jozi Bora"),
    ("Daily", "Kila Siku"),
    ("Daily Report", "Ripoti ya Kila Siku"),
    ("Performance Stats", "Takwimu za Utendaji"),
    ("Recent History", "Historia ya Hivi Karibuni"),
    ("Explain ATLAX", "Eleza ATLAX"),
    ("Review History", "Historia ya Mapitio"),
    ("Trading Plan Notes", "Maelezo ya Mpango wa Trading"),
    ("News", "Habari"),
    ("Reset AI Quota", "Weka Upya Kiwango cha AI"),
    ("Block ATLAX AI", "Zuia ATLAX AI"),
    ("Unblock ATLAX AI", "Ruhusu ATLAX AI"),
    ("Alerts & System", "Arifa na Mfumo"),
    ("Heatmap", "Heatmap"),
    ("Recent Activity", "Shughuli za Hivi Karibuni"),
    ("Last 30 Days", "Siku 30 Zilizopita"),
    ("AI Assistant", "Msaidizi wa AI"),
    ("Support", "Msaada"),
    ("Today", "Leo"),
    ("Last 7 Days", "Siku 7 Zilizopita"),
    ("Last 30 Days", "Siku 30 Zilizopita"),
    ("All Time", "Muda Wote"),
)


def localize_text(text: Any, language: Any = "en") -> str:
    """Translate existing outbound prose while preserving dynamic values."""
    value = str(text or "")
    if normalize_language(language) == "en" or not value:
        return value
    for source, target in _SW_TEXT_REPLACEMENTS:
        value = re.sub(re.escape(source), target, value, flags=re.IGNORECASE)
    return value


def localize_button_text(text: Any, language: Any = "en") -> str:
    value = str(text or "")
    if normalize_language(language) == "en" or not value:
        return value
    suffix = ""
    if value.endswith(" ✅"):
        value, suffix = value[:-2], " ✅"
    # Telegram buttons commonly carry a leading emoji. Translate the label
    # beneath it while keeping the visual cue intact.
    prefix = ""
    body = value
    if " " in value:
        head, remainder = value.split(" ", 1)
        if head and not head[0].isalnum() and not head.startswith("<"):
            prefix = f"{head} "
            body = remainder
    for source, target in sorted(_SW_BUTTON_TRANSLATIONS, key=lambda item: len(item[0]), reverse=True):
        if body == source:
            return prefix + target + suffix
        if body.startswith(source + " "):
            return prefix + target + body[len(source):] + suffix
    return value + suffix


def localize_keyboard(keyboard: Any, language: Any = "en") -> list[list[dict]]:
    if not isinstance(keyboard, list) or normalize_language(language) == "en":
        return keyboard
    localized: list[list[dict]] = []
    for row in keyboard:
        localized_row: list[dict] = []
        for button in row if isinstance(row, list) else []:
            item = dict(button) if isinstance(button, dict) else {"text": str(button)}
            if "text" in item:
                item["text"] = localize_button_text(item["text"], language)
            localized_row.append(item)
        localized.append(localized_row)
    return localized


def localize_outbound(text: Any, keyboard: Any, language: Any = "en") -> tuple[str, list[list[dict]]]:
    return localize_text(text, language), localize_keyboard(keyboard, language)
