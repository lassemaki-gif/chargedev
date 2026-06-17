export interface MarketT {
  kicker: string;
  headline1: string;
  headline2: string;
  body: string;
  findCharger: string;
  becomeHost: string;
  pkg: string; // "packages from"
}

export interface Market {
  id: string;
  countryNameEn: string;
  currencySymbol: string;
  pricePerKwh: number;
  locale: string;
  cities: string;
  t: MarketT;
}

// ── Language templates ────────────────────────────────────────────────────────

const LANG: Record<string, Omit<MarketT, "kicker">> = {
  en: {
    headline1: "Charge smarter,",
    headline2: "earn from home.",
    body: "Find home EV chargers near you, or earn money by renting out your own socket. Book 20–80 kWh packages — PIN access, instant confirmation.",
    findCharger: "Find a charger",
    becomeHost: "Become a host",
    pkg: "Packages from",
  },
  fi: {
    headline1: "Lataa lähellä,",
    headline2: "ansaitse kotoa.",
    body: "Löydä kotilatauspisteitä läheltäsi tai ansaitse rahaa vuokraamalla omaa pistorasiaasi. Varaa 20–80 kWh paketteja — PIN-pääsy, välitön vahvistus.",
    findCharger: "Etsi latauspiste",
    becomeHost: "Ryhdy isännäksi",
    pkg: "Paketit alkaen",
  },
  sv: {
    headline1: "Ladda nära dig,",
    headline2: "tjäna hemifrån.",
    body: "Hitta hemmaladdare nära dig eller tjäna pengar på att hyra ut ditt uttag. Boka 20–80 kWh paket — PIN-åtkomst, omedelbar bekräftelse.",
    findCharger: "Hitta en laddare",
    becomeHost: "Bli värd",
    pkg: "Paket från",
  },
  nb: {
    headline1: "Lad nær deg,",
    headline2: "tjen hjemmefra.",
    body: "Finn hjemmeladere nær deg eller tjen penger ved å leie ut kontakten din. Bestill 20–80 kWh pakker — PIN-tilgang, umiddelbar bekreftelse.",
    findCharger: "Finn en lader",
    becomeHost: "Bli vert",
    pkg: "Pakker fra",
  },
  da: {
    headline1: "Oplad tæt på dig,",
    headline2: "tjen hjemmefra.",
    body: "Find hjemmeladere tæt på dig eller tjen penge ved at udleje din stikkontakt. Book 20–80 kWh pakker — PIN-adgang, øjeblikkelig bekræftelse.",
    findCharger: "Find en lader",
    becomeHost: "Bliv vært",
    pkg: "Pakker fra",
  },
  de: {
    headline1: "Laden in der Nähe,",
    headline2: "verdienen von zu Hause.",
    body: "Finden Sie Heimlader in Ihrer Nähe oder verdienen Sie Geld durch Vermietung Ihrer Steckdose. Buchen Sie 20–80 kWh Pakete — PIN-Zugang, sofortige Bestätigung.",
    findCharger: "Ladestation finden",
    becomeHost: "Gastgeber werden",
    pkg: "Pakete ab",
  },
  fr: {
    headline1: "Rechargez près de chez vous,",
    headline2: "gagnez depuis chez vous.",
    body: "Trouvez des chargeurs domestiques près de vous ou gagnez de l'argent en louant votre prise. Réservez des forfaits 20–80 kWh — accès PIN, confirmation instantanée.",
    findCharger: "Trouver un chargeur",
    becomeHost: "Devenir hôte",
    pkg: "Forfaits à partir de",
  },
  nl: {
    headline1: "Laad op in de buurt,",
    headline2: "verdien thuis.",
    body: "Vind thuisladers bij jou in de buurt of verdien geld door je stopcontact te verhuren. Boek 20–80 kWh pakketten — PIN-toegang, directe bevestiging.",
    findCharger: "Zoek een lader",
    becomeHost: "Word host",
    pkg: "Pakketten vanaf",
  },
  es: {
    headline1: "Carga cerca de ti,",
    headline2: "gana desde casa.",
    body: "Encuentra cargadores domésticos cerca de ti o gana dinero alquilando tu enchufe. Reserva paquetes de 20–80 kWh — acceso por PIN, confirmación instantánea.",
    findCharger: "Encontrar cargador",
    becomeHost: "Ser anfitrión",
    pkg: "Paquetes desde",
  },
  pt: {
    headline1: "Carregue perto de si,",
    headline2: "ganhe em casa.",
    body: "Encontre carregadores domésticos perto de si ou ganhe dinheiro alugando a sua tomada. Reserve pacotes de 20–80 kWh — acesso por PIN, confirmação instantânea.",
    findCharger: "Encontrar carregador",
    becomeHost: "Tornar-se anfitrião",
    pkg: "Pacotes a partir de",
  },
  ptbr: {
    headline1: "Carregue perto de você,",
    headline2: "ganhe de casa.",
    body: "Encontre carregadores residenciais perto de você ou ganhe dinheiro alugando sua tomada. Reserve pacotes de 20–80 kWh — acesso por PIN, confirmação imediata.",
    findCharger: "Encontrar carregador",
    becomeHost: "Ser anfitrião",
    pkg: "Pacotes a partir de",
  },
  it: {
    headline1: "Ricarica vicino a te,",
    headline2: "guadagna da casa.",
    body: "Trova caricatori domestici vicino a te o guadagna affittando la tua presa. Prenota pacchetti da 20–80 kWh — accesso PIN, conferma istantanea.",
    findCharger: "Trova caricatore",
    becomeHost: "Diventa host",
    pkg: "Pacchetti da",
  },
  pl: {
    headline1: "Ładuj w pobliżu,",
    headline2: "zarabiaj z domu.",
    body: "Znajdź domowe ładowarki w pobliżu lub zarabiaj, wynajmując swoje gniazdko. Rezerwuj pakiety 20–80 kWh — dostęp PIN, natychmiastowe potwierdzenie.",
    findCharger: "Znajdź ładowarkę",
    becomeHost: "Zostań hostem",
    pkg: "Pakiety od",
  },
  cs: {
    headline1: "Nabíjejte v blízkosti,",
    headline2: "vydělávejte doma.",
    body: "Najděte domácí nabíječky ve vašem okolí nebo vydělávejte pronájmem vaší zásuvky. Rezervujte balíčky 20–80 kWh — přístup PIN, okamžité potvrzení.",
    findCharger: "Najít nabíječku",
    becomeHost: "Stát se hostitelem",
    pkg: "Balíčky od",
  },
  ro: {
    headline1: "Încărcați în apropiere,",
    headline2: "câștigați de acasă.",
    body: "Găsiți încărcătoare acasă în apropierea dvs. sau câștigați bani închiriind priza dvs. Rezervați pachete de 20–80 kWh — acces PIN, confirmare instantanee.",
    findCharger: "Găsește un încărcător",
    becomeHost: "Deveniți gazdă",
    pkg: "Pachete de la",
  },
  el: {
    headline1: "Φορτίστε κοντά σας,",
    headline2: "κερδίστε από το σπίτι.",
    body: "Βρείτε οικιακούς φορτιστές κοντά σας ή κερδίστε χρήματα νοικιάζοντας την πρίζα σας. Κρατήστε πακέτα 20–80 kWh — πρόσβαση με PIN, άμεση επιβεβαίωση.",
    findCharger: "Εύρεση φορτιστή",
    becomeHost: "Γίνετε οικοδεσπότης",
    pkg: "Πακέτα από",
  },
  ja: {
    headline1: "近くで充電、",
    headline2: "自宅で稼ぐ。",
    body: "近くの自宅充電スポットを探すか、自分のコンセントを貸して収入を得ましょう。20〜80 kWhのパッケージを予約 — PINコードでアクセス、即時確認。",
    findCharger: "充電スポットを探す",
    becomeHost: "ホストになる",
    pkg: "パッケージ",
  },
  ko: {
    headline1: "가까운 곳에서 충전하고,",
    headline2: "집에서 수익을 내세요.",
    body: "가까운 가정용 충전기를 찾거나, 내 콘센트를 대여하여 수익을 창출하세요. 20–80 kWh 패키지 예약 — PIN 접근, 즉시 확인.",
    findCharger: "충전기 찾기",
    becomeHost: "호스트 되기",
    pkg: "패키지",
  },
  th: {
    headline1: "ชาร์จใกล้บ้าน,",
    headline2: "สร้างรายได้จากบ้าน.",
    body: "ค้นหาจุดชาร์จในบ้านใกล้ๆ คุณ หรือสร้างรายได้ด้วยการให้เช่าเต้ารับไฟฟ้าของคุณ จองแพ็กเกจ 20–80 kWh — เข้าถึงด้วย PIN ยืนยันทันที",
    findCharger: "ค้นหาจุดชาร์จ",
    becomeHost: "เป็นเจ้าบ้าน",
    pkg: "แพ็กเกจเริ่มต้น",
  },
  id: {
    headline1: "Isi daya di dekat Anda,",
    headline2: "hasilkan uang dari rumah.",
    body: "Temukan pengisi daya rumah di dekat Anda atau hasilkan uang dengan menyewakan colokan Anda. Pesan paket 20–80 kWh — akses PIN, konfirmasi instan.",
    findCharger: "Cari pengisi daya",
    becomeHost: "Jadi tuan rumah",
    pkg: "Paket mulai dari",
  },
  zhtw: {
    headline1: "附近充電，",
    headline2: "在家賺錢。",
    body: "在附近尋找家用充電站，或出租您的插座賺取收入。預訂 20–80 kWh 套餐 — PIN碼訪問，即時確認。",
    findCharger: "尋找充電站",
    becomeHost: "成為房東",
    pkg: "套餐",
  },
};

function t(lang: string, country: string): MarketT {
  const l = LANG[lang] ?? LANG.en;
  return { ...l, kicker: `EV CHARGING · ${country.toUpperCase()}` };
}

// ── Market definitions ────────────────────────────────────────────────────────
// pricePerKwh = local avg + 30%, rounded to give nice package totals

const raw: Array<{
  id: string; countryNameEn: string; currencySymbol: string;
  pricePerKwh: number; locale: string; cities: string; lang: string;
}> = [
  // European Union
  { id: "at", countryNameEn: "Austria",        currencySymbol: "€",    pricePerKwh: 0.35, locale: "de-AT", cities: "Wien · Graz · Linz",            lang: "de" },
  { id: "be", countryNameEn: "Belgium",         currencySymbol: "€",    pricePerKwh: 0.45, locale: "nl-BE", cities: "Brussel · Antwerpen · Gent",     lang: "nl" },
  { id: "bg", countryNameEn: "Bulgaria",        currencySymbol: "лв",   pricePerKwh: 0.29, locale: "bg-BG", cities: "София · Пловдив · Варна",        lang: "en" },
  { id: "hr", countryNameEn: "Croatia",         currencySymbol: "€",    pricePerKwh: 0.20, locale: "hr-HR", cities: "Zagreb · Split · Rijeka",         lang: "en" },
  { id: "cy", countryNameEn: "Cyprus",          currencySymbol: "€",    pricePerKwh: 0.30, locale: "el-CY", cities: "Λευκωσία · Λεμεσός",            lang: "el" },
  { id: "cz", countryNameEn: "Czechia",         currencySymbol: "Kč",   pricePerKwh: 7.00, locale: "cs-CZ", cities: "Praha · Brno · Ostrava",         lang: "cs" },
  { id: "dk", countryNameEn: "Denmark",         currencySymbol: "kr",   pricePerKwh: 5.00, locale: "da-DK", cities: "København · Aarhus · Odense",    lang: "da" },
  { id: "ee", countryNameEn: "Estonia",         currencySymbol: "€",    pricePerKwh: 0.25, locale: "et-EE", cities: "Tallinn · Tartu · Narva",         lang: "en" },
  { id: "fi", countryNameEn: "Finland",         currencySymbol: "€",    pricePerKwh: 0.25, locale: "fi-FI", cities: "Helsinki · Tampere · Espoo",      lang: "fi" },
  { id: "fr", countryNameEn: "France",          currencySymbol: "€",    pricePerKwh: 0.35, locale: "fr-FR", cities: "Paris · Lyon · Marseille",        lang: "fr" },
  { id: "de", countryNameEn: "Germany",         currencySymbol: "€",    pricePerKwh: 0.55, locale: "de-DE", cities: "Berlin · Hamburg · München",      lang: "de" },
  { id: "gr", countryNameEn: "Greece",          currencySymbol: "€",    pricePerKwh: 0.35, locale: "el-GR", cities: "Αθήνα · Θεσσαλονίκη · Πάτρα",  lang: "el" },
  { id: "hu", countryNameEn: "Hungary",         currencySymbol: "Ft",   pricePerKwh: 130,  locale: "hu-HU", cities: "Budapest · Debrecen · Pécs",      lang: "en" },
  { id: "ie", countryNameEn: "Ireland",         currencySymbol: "€",    pricePerKwh: 0.60, locale: "en-IE", cities: "Dublin · Cork · Galway",          lang: "en" },
  { id: "it", countryNameEn: "Italy",           currencySymbol: "€",    pricePerKwh: 0.40, locale: "it-IT", cities: "Roma · Milano · Napoli",          lang: "it" },
  { id: "lv", countryNameEn: "Latvia",          currencySymbol: "€",    pricePerKwh: 0.30, locale: "lv-LV", cities: "Rīga · Daugavpils · Jelgava",    lang: "en" },
  { id: "lt", countryNameEn: "Lithuania",       currencySymbol: "€",    pricePerKwh: 0.25, locale: "lt-LT", cities: "Vilnius · Kaunas · Klaipėda",    lang: "en" },
  { id: "lu", countryNameEn: "Luxembourg",      currencySymbol: "€",    pricePerKwh: 0.25, locale: "fr-LU", cities: "Luxembourg · Esch · Differdange", lang: "fr" },
  { id: "mt", countryNameEn: "Malta",           currencySymbol: "€",    pricePerKwh: 0.20, locale: "en-MT", cities: "Valletta · Birkirkara · Qormi",   lang: "en" },
  { id: "nl", countryNameEn: "Netherlands",     currencySymbol: "€",    pricePerKwh: 0.55, locale: "nl-NL", cities: "Amsterdam · Rotterdam · Den Haag", lang: "nl" },
  { id: "pl", countryNameEn: "Poland",          currencySymbol: "zł",   pricePerKwh: 1.25, locale: "pl-PL", cities: "Warszawa · Kraków · Wrocław",     lang: "pl" },
  { id: "pt", countryNameEn: "Portugal",        currencySymbol: "€",    pricePerKwh: 0.30, locale: "pt-PT", cities: "Lisboa · Porto · Amadora",        lang: "pt" },
  { id: "ro", countryNameEn: "Romania",         currencySymbol: "RON",  pricePerKwh: 1.50, locale: "ro-RO", cities: "București · Cluj · Timișoara",    lang: "ro" },
  { id: "sk", countryNameEn: "Slovakia",        currencySymbol: "€",    pricePerKwh: 0.30, locale: "sk-SK", cities: "Bratislava · Košice · Prešov",    lang: "en" },
  { id: "si", countryNameEn: "Slovenia",        currencySymbol: "€",    pricePerKwh: 0.30, locale: "sl-SI", cities: "Ljubljana · Maribor · Celje",     lang: "en" },
  { id: "es", countryNameEn: "Spain",           currencySymbol: "€",    pricePerKwh: 0.35, locale: "es-ES", cities: "Madrid · Barcelona · Valencia",   lang: "es" },
  { id: "se", countryNameEn: "Sweden",          currencySymbol: "kr",   pricePerKwh: 3.00, locale: "sv-SE", cities: "Stockholm · Göteborg · Malmö",   lang: "sv" },
  // Non-EU Europe
  { id: "gb", countryNameEn: "United Kingdom",  currencySymbol: "£",    pricePerKwh: 0.40, locale: "en-GB", cities: "London · Manchester · Edinburgh", lang: "en" },
  { id: "ch", countryNameEn: "Switzerland",     currencySymbol: "CHF",  pricePerKwh: 0.40, locale: "de-CH", cities: "Zürich · Genève · Basel",         lang: "de" },
  { id: "no", countryNameEn: "Norway",          currencySymbol: "kr",   pricePerKwh: 2.00, locale: "nb-NO", cities: "Oslo · Bergen · Trondheim",       lang: "nb" },
  { id: "is", countryNameEn: "Iceland",         currencySymbol: "kr",   pricePerKwh: 35,   locale: "is-IS", cities: "Reykjavík · Akureyri · Kópavogur", lang: "en" },
  // Americas & Oceania
  { id: "us", countryNameEn: "United States",   currencySymbol: "$",    pricePerKwh: 0.20, locale: "en-US", cities: "New York · LA · Chicago",         lang: "en" },
  { id: "ca", countryNameEn: "Canada",          currencySymbol: "CA$",  pricePerKwh: 0.25, locale: "en-CA", cities: "Toronto · Vancouver · Calgary",   lang: "en" },
  { id: "au", countryNameEn: "Australia",       currencySymbol: "A$",   pricePerKwh: 0.40, locale: "en-AU", cities: "Sydney · Melbourne · Brisbane",   lang: "en" },
  { id: "nz", countryNameEn: "New Zealand",     currencySymbol: "NZ$",  pricePerKwh: 0.40, locale: "en-NZ", cities: "Auckland · Wellington · Christchurch", lang: "en" },
  // Asia
  { id: "jp", countryNameEn: "Japan",           currencySymbol: "¥",    pricePerKwh: 40,   locale: "ja-JP", cities: "東京 · 大阪 · 名古屋",             lang: "ja" },
  { id: "kr", countryNameEn: "South Korea",     currencySymbol: "₩",    pricePerKwh: 200,  locale: "ko-KR", cities: "서울 · 부산 · 인천",               lang: "ko" },
  { id: "hk", countryNameEn: "Hong Kong",       currencySymbol: "HK$",  pricePerKwh: 2.00, locale: "zh-HK", cities: "港島 · 九龍 · 新界",              lang: "zhtw" },
  { id: "tw", countryNameEn: "Taiwan",          currencySymbol: "NT$",  pricePerKwh: 5.00, locale: "zh-TW", cities: "台北 · 台中 · 台南",              lang: "zhtw" },
  { id: "sg", countryNameEn: "Singapore",       currencySymbol: "S$",   pricePerKwh: 0.40, locale: "en-SG", cities: "Singapore",                       lang: "en" },
  { id: "th", countryNameEn: "Thailand",        currencySymbol: "฿",    pricePerKwh: 6.00, locale: "th-TH", cities: "กรุงเทพฯ · เชียงใหม่ · ภูเก็ต", lang: "th" },
  { id: "my", countryNameEn: "Malaysia",        currencySymbol: "RM",   pricePerKwh: 0.50, locale: "ms-MY", cities: "Kuala Lumpur · Johor Bahru · Penang", lang: "en" },
  { id: "ph", countryNameEn: "Philippines",     currencySymbol: "₱",    pricePerKwh: 15,   locale: "en-PH", cities: "Manila · Cebu · Davao",           lang: "en" },
  { id: "id", countryNameEn: "Indonesia",       currencySymbol: "Rp",   pricePerKwh: 2000, locale: "id-ID", cities: "Jakarta · Surabaya · Bandung",    lang: "id" },
  // Latin America
  { id: "mx", countryNameEn: "Mexico",          currencySymbol: "$",    pricePerKwh: 2.00, locale: "es-MX", cities: "CDMX · Guadalajara · Monterrey",  lang: "es" },
  { id: "br", countryNameEn: "Brazil",          currencySymbol: "R$",   pricePerKwh: 1.25, locale: "pt-BR", cities: "São Paulo · Rio de Janeiro · BH",  lang: "ptbr" },
  { id: "co", countryNameEn: "Colombia",        currencySymbol: "$",    pricePerKwh: 1000, locale: "es-CO", cities: "Bogotá · Medellín · Cali",        lang: "es" },
  { id: "pe", countryNameEn: "Peru",            currencySymbol: "S/",   pricePerKwh: 0.80, locale: "es-PE", cities: "Lima · Arequipa · Trujillo",      lang: "es" },
  { id: "cl", countryNameEn: "Chile",           currencySymbol: "$",    pricePerKwh: 200,  locale: "es-CL", cities: "Santiago · Valparaíso · Concepción", lang: "es" },
  { id: "ar", countryNameEn: "Argentina",       currencySymbol: "$",    pricePerKwh: 260,  locale: "es-AR", cities: "Buenos Aires · Córdoba · Rosario", lang: "es" },
  { id: "pr", countryNameEn: "Puerto Rico",     currencySymbol: "$",    pricePerKwh: 0.40, locale: "es-PR", cities: "San Juan · Ponce · Bayamón",      lang: "es" },
];

export const markets: Market[] = raw.map((m) => ({
  ...m,
  t: t(m.lang, m.countryNameEn),
}));

export const marketById = Object.fromEntries(markets.map((m) => [m.id, m]));

export function packagePrice(market: Market, kwh: number): string {
  const total = market.pricePerKwh * kwh;
  const sym = market.currencySymbol;
  // Format large integers (JPY, KRW, IDR, etc.) without decimals
  if (market.pricePerKwh >= 10) return `${sym}${Math.round(total).toLocaleString()}`;
  return `${sym}${total % 1 === 0 ? total.toFixed(0) : total.toFixed(2)}`;
}
