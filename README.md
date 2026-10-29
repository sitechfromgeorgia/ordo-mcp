<p align="center">
  <h1 align="center">⚖️ Matsne.gov.ge MCP Server</h1>
  <p align="center">
    <strong>Search, read and analyze Georgia's official legislative database — through AI</strong><br>
    <em>საქართველოს საკანონმდებლო მაცნე — ნებისმიერი AI ასისტენტიდან</em>
  </p>
  <p align="center">
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT License"></a>
    <img src="https://img.shields.io/badge/Python-3.10+-brightgreen" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/MCP-1.0+-purple" alt="MCP 1.0+">
  </p>
</p>

<p align="center"><strong><code>🇬🇪 შექმნილია სიყვარულით ბათუმიდან, საქართველო — Made with love from Batumi, Georgia</code></strong></p>

---

## ✨ One-click setup — copy this to Claude

> **Non-technical?** Just copy the box below, paste it to Claude or any AI assistant, and it will set up everything for you — download, install, configure, and test.

<pre>
I want to use the Matsne.gov.ge MCP server from https://github.com/sitechfromgeorgia/matsne-gov-ge-mcp

Please help me set it up. Here's what I need you to do:

1. Check if Python 3.10+ is installed — if not, tell me to download it from https://www.python.org/downloads/
2. Check if `uv` is installed — if not:
   - Windows (PowerShell): `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
   - Mac/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
3. Download the project:
   - If git is installed: `git clone https://github.com/sitechfromgeorgia/matsne-gov-ge-mcp`
   - Otherwise: download the ZIP from the GitHub page and extract it
4. Navigate to the project folder and run: `uv run mcp_server.py`
5. Tell me the full folder path so I can configure Claude Desktop
6. Tell me to:
   - Open Claude Desktop → Settings → Developer → Edit Config
   - Add this (replace PATH with my actual folder path):
```json
{
  "mcpServers": {
    "matsne-ge": {
      "command": "uv",
      "args": ["--directory", "PATH", "run", "mcp_server.py"]
    }
  }
}
```
7. Restart Claude Desktop and verify the 🔧 icon appears

Please walk me through each step and check as we go.
</pre>

---

## 🇬🇪 ქართული

### რა არის ეს?

ეს არის **MCP (Model Context Protocol) სერვერი**, რომელიც საშუალებას აძლევს ნებისმიერ AI ასისტენტს (Claude Desktop, VS Code, Cursor, Windsurf) **მოძებნოს, წაიკითხოს და გააანალიზოს** დოკუმენტები [matsne.gov.ge](https://matsne.gov.ge)-დან — საქართველოს საკანონმდებლო მაცნე.

**What?** An MCP server that connects AI assistants to Georgia's official legislative database.

**Why?** იმისთვის, რომ AI-ს შეეძლოს ქართული კანონმდებლობის რეალურ დროში ძებნა, წაკითხვა და ანალიზი — მარტივი, ბუნებრივი ენით.

### 🛠️ ხელმისაწვდომი ინსტრუმენტები

| Tool | რას აკეთებს |
|---|---|
| `matsne_suggest` | სწრაფი ძებნა საკვანძო სიტყვით |
| `matsne_search` | ძებნა ფილტრებით (თარიღი, ტიპი, ორგანო, ნომერი) |
| `matsne_get_document` | დოკუმენტი: მეტამონაცემები + ტექსტი + სტრუქტურა + კომენტარები (ნებისმიერი რედაქცია) |
| `matsne_get_article` | კონკრეტული მუხლი ციტატით ("სამოქალაქო კოდექსის 361-ე მუხლი"), ნაწილით და თარიღით მოქმედი რედაქციით |
| `matsne_list_versions` | დოკუმენტის ყველა რედაქცია + ცვლილების შემტანი აქტები |
| `matsne_compare_versions` | ორ რედაქციას შორის რა შეიცვალა — მუხლების მიხედვით |
| `matsne_check_status` | მოქმედია თუ ძალადაკარგული, ბოლო ცვლილებები |
| `matsne_get_tree` | დოკუმენტის სტრუქტურის ხე (მუხლები, თავები) |
| `matsne_get_back_references` | რომელი დოკუმენტები ეყრდნობა ამას |
| `matsne_get_comments` | კომენტარები |
| `matsne_get_linked` | კონკრეტული მუხლის ტექსტი |
| `matsne_search_by_keyword` | მხოლოდ აქტიური დოკუმენტების ძებნა |
| `matsne_has_today` | არის თუ არა დღევანდელი დოკუმენტები |
| `matsne_today_documents` | დღეს გამოქვეყნებული დოკუმენტები |

### ⚖️ სასამართლო პრაქტიკა (ახალი)

| Tool | რას აკეთებს |
|---|---|
| `court_search_decisions` | საერთო სასამართლოების გადაწყვეტილებების ძებნა (საქმის ნომერი, თარიღი, ინსტანცია, სასამართლო, მუხლი, ტექსტი) |
| `court_get_decision` | გადაწყვეტილების სრული ტექსტი |
| `court_classifiers` | ფილტრების ლექსიკონები (ინსტანციები, სასამართლოები, კატეგორიები, მუხლები) |
| `court_verify_act` | სასამართლო აქტის გადამოწმება შტრიხკოდით + საქმის ნომრით |
| `supreme_search` | უზენაესი სასამართლოს გადაწყვეტილებების ძებნა (პალატა, მოსამართლე, შედეგი, ტექსტი) |
| `supreme_get_case` | უზენაესის გადაწყვეტილების სრული ტექსტი |
| `supreme_classifiers` | შედეგის/საჩივრის სახის კოდები |
| `supreme_grand_chamber` | დიდი პალატის გადაწყვეტილებები + ნორმათა განმარტებები (სავალდებულო) |

ყველა სასამართლო შედეგს აქვს `authority_level` ველი (5=დიდი პალატა → 2=პირველი ინსტანცია),
რაც AI-ს საშუალებას აძლევს სწორად შეაფასოს პრეცედენტის წონა.

### 🏛️ უმაღლესი სასამართლოები (ახალი)

| Tool | რას აკეთებს |
|---|---|
| `constitutional_search_acts` | საკონსტიტუციო სასამართლოს აქტების ძებნა (სახელი, ნომერი, ტექსტი) |
| `constitutional_get_act` | საკონსტიტუციო აქტის სრული ტექსტი |
| `echr_search` | ადამიანის უფლებათა ევროპული სასამართლოს (HUDOC) ძებნა |
| `echr_get_case` | ECHR გადაწყვეტილების სრული ტექსტი |

### 🏛️ პარლამენტი — კანონპროექტები (ახალი)

| Tool | რას აკეთებს |
|---|---|
| `parliament_search_bills` | კანონპროექტების ძებნა — რა იცვლება მალე კანონმდებლობაში |
| `parliament_get_bill` | კანონპროექტის ისტორია: მოსმენები, ხმები, კომიტეტები, ფაილები |

### 🔬 იურიდიული კვლევა (ახალი)

| Tool | რას აკეთებს |
|---|---|
| `verify_citation` | ამოწმებს, მართლა წერია თუ არა ციტატა მითითებულ კანონში/გადაწყვეტილებაში (ჰალუცინაციის წინააღმდეგ) |
| `research_article` | მუხლი + მისი სასამართლო პრაქტიკა ერთი ზარით, ავტორიტეტის მიხედვით |

### 📚 უზენაესის ანალიტიკა, ვადები, ბაჟი, PDF (ახალი)

| Tool | რას აკეთებს |
|---|---|
| `supreme_practice_search` / `_list` / `_get` / `_categories` | პრაქტიკის განზოგადებები, რეკომენდაციები, მნიშვნელოვანი განმარტებები, გადაწყვეტილებათა კრებულები, ჟურნალი |
| `deadline_calculate` / `deadline_get_rule` / `deadline_calculate_custom` / `deadline_list_procedures` / `deadline_holidays` | საპროცესო ვადა ცოცხალი ნორმიდან (ციტატით, უქმე დღეებით, გაფრთხილებით) — მეხსიერებიდან არასდროს |
| `fee_calculate` / `fee_get_rule` / `fee_list_types` | სახელმწიფო ბაჟი სსსკ 39-ე მუხლის ცოცხალი ტექსტიდან (%, მინიმუმი, ზედა ზღვარი ფიზიკური/იურიდიული პირისთვის, მაგისტრატი, მორიგება) — გაფრთხილებით |
| `pdf_get_text` / `pdf_search` | PDF-ის ტექსტი და ძებნა (ძველი AcadNusx/LitNusx ფონტების ავტომატური გასწორებით) |
| `court_search_article` | საერთო სასამართლოების გადაწყვეტილებები კონკრეტულ მუხლზე (ecd: 2018–2020) |

### 🗂️ საჯარო რეესტრები (ახალი)

| Tool | რას აკეთებს |
|---|---|
| `registry_company_search` / `_participations` / `_applications` | მეწარმეთა რეესტრი: კომპანია ს/კ-ით ან სახელით (სტატუსი), დამფუძნებელი/პარტნიორი, განცხადებები (ლიკვიდაცია, გადახდისუუნარობა, წილის გირავნობა) |
| `registry_property_parcel` / `registry_property_applications` | უძრავი ქონება საკადასტრო კოდით: მესაკუთრე, ფართი, ამონაწერის PDF; განცხადებების ჟურნალი |
| `registry_liens` | ყადაღა, აკრძალვა, საგადასახადო გირავნობა (საფუძვლით — მაგ. სასამართლოს განჩინება) |
| `registry_movable_pledges` / `_get` | მოძრავი ნივთების/არამატერიალური ქონების გირავნობა და ლიზინგი |
| `registry_lawyer_search` / `registry_notary_search` | ადვოკატთა ერთიანი სია, სანოტარო ბიუროები |
| `registry_procurement_list` / `_search` / `_get` | შესყიდვების შავი, თეთრი და გაფრთხილებულთა სიები |
| `companyinfo_company` / `_company_search` | companyinfo.ge (საერთაშორისო გამჭვირვალობა): კომპანიის სრული ბარათი — დირექტორები/პარტნიორები და მათი ისტორია, წილები, სანქციები, შავი სია, დეკლარაციები, ფინანსური ანგარიშგება |
| `companyinfo_people_search` / `companyinfo_person` | პირის ბიზნეს-როლები სახელით ან პირადი ნომრით (სეხნიების გაფრთხილებით; პირადი ნომრით გადამოწმება სხვა რეესტრებში) |
| `geostat_search` / `geostat_company` / `_activity_codes` / `_regions` | საქსტატის ბიზნეს რეგისტრი: საქმიანობა, ზომა, მისამართები, ხელმძღვანელი, ისტორია; ფილტრები საქმიანობით/რეგიონით |

მხოლოდ ღია (CAPTCHA-ს გარეშე) წყაროები; ძებნა ს/კ-ით, პირადი ნომრით, საკადასტრო კოდით, ხოლო პირის — სახელითაც (companyinfo, მხოლოდ ბიზნეს-როლები, სეხნიების გაფრთხილებით); ყველა პასუხს პერსონალურ მონაცემებზე გაფრთხილება ახლავს.

### 🏢 ადმინისტრაციული ორგანოები (ახალი)

| Tool | რას აკეთებს |
|---|---|
| `tax_dispute_search` / `_get` | ფინანსთა სამინისტროს დავების განხილვის საბჭოს გადაწყვეტილებები (სრული ტექსტი) |
| `rs_dispute_search` / `_get` | შემოსავლების სამსახურის დავების საბჭო |
| `tax_guidance_search` / `_get` | საჯარო გადაწყვეტილებები, სიტუაციური სახელმძღვანელოები |
| `competition_search` | კონკურენციისა და მომხმარებლის დაცვის სააგენტო |
| `ombudsman_search` / `_get` | სახალხო დამცველის რეკომენდაციები და წინადადებები |
| `procurement_dispute_search` / `_get` | სახელმწიფო შესყიდვების დავების საბჭო |
| `bar_ethics_search` | ადვოკატთა ასოციაციის ეთიკის კომისია |

**Prompts (შაბლონები):** `legal_memo` (იურიდიული მემორანდუმი), `case_analysis` (IRAC ანალიზი),
`law_change_review` (რა შეიცვალა კანონში + მოლოდინში მყოფი კანონპროექტები).

### 📖 ინსტალაცია

#### 1. Python-ის დაყენება

👉 [python.org/downloads](https://www.python.org/downloads/) — ჩამოტვირთეთ და დააყენეთ. **Windows-ზე მონიშნეთ "Add Python to PATH".**

#### 2. uv-ის დაყენება

**Windows — PowerShell-ში ჩაწერეთ:**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Mac/Linux:**
```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

> **დააყენების შემდეგ გადატვირთეთ ტერმინალი.**

#### 3. პროექტის ჩამოტვირთვა

**Git-ით:**
```sh
git clone https://github.com/sitechfromgeorgia/matsne-gov-ge-mcp
cd matsne-gov-ge-mcp
```

**ZIP-ით:** GitHub-ზე → `Code` → `Download ZIP` → გახსენით

#### 4. MCP სერვერის გაშვება

```sh
uv run mcp_server.py
```

> **ვერაფერს ხედავთ?** ეს ნორმალურია — სერვერი მუშაობს ფონზე. გამოსასვლელად `Ctrl+C`.

#### 5. Claude Desktop-თან დაკავშირება

**Claude Desktop** → Settings → Developer → Edit Config → ჩასვით:

```json
{
  "mcpServers": {
    "matsne-ge": {
      "command": "uv",
      "args": ["--directory", "C:\\Users\\თქვენი_სახელი\\matsne-gov-ge-mcp", "run", "mcp_server.py"]
    }
  }
}
```

> **მნიშვნელოვანი:** `C:\\Users\\თქვენი_სახელი\\...` — შეცვალეთ იმ გზით, სადაც პროექტი ჩამოტვირთეთ.

**გადატვირთეთ Claude Desktop.** თუ 🔧 გამოჩნდა — მზადაა 🎉

### 💬 ინსტრუქციის მაგალითები

სცადეთ ეს მოთხოვნები:

- _"მოძებნე დღევანდელი გამოქვეყნებული დოკუმენტები"_
- _"მოიძიე კანონები შრომის შესახებ ბოლო 30 დღეში"_
- _"რა წერია 6840140 დოკუმენტში? გააანალიზე"_
- _"რა ეწერა სამოქალაქო კოდექსის 1145-ე მუხლში 2020 წლის 1 იანვარს და რა შეიცვალა მას შემდეგ?"_
- _"ვინ მიიღო 6840140, როდის გამოქვეყნდა, რა ტიპისაა?"_
- _"მოძებნე 'პერსონალური მონაცემები' — აქტიური კანონების სია"_
- _"შეამოწმე რა კანონები გამოქვეყნდა დღეს"_

### ⚠️ Troubleshooting

| პრობლემა | გამოსავალი |
|---|---|
| "uv is not recognized" | uv არ დაყენებულა, გადახედეთ ნაბიჯ 2-ს |
| "Python not found" | Python არ დაყენებულა, ან "Add to PATH" არ მონიშნეთ |
| "No module named mcp" | გაუშვით `uv run mcp_server.py` (არა `python mcp_server.py`) |
| 🔧 არ ჩანს Claude-ში | JSON config-ში ბილიკი შეამოწმეთ |
| "Access Denied" | API endpoint-ები მუშაობს — მთავარი ფუნქციონალი ხელმისაწვდომია |

---

## 🇬🇧 English

### What is this?

An **MCP server** that connects AI assistants to [matsne.gov.ge](https://matsne.gov.ge) — Georgia's official legislative database. Search, read, and analyze Georgian laws through natural conversation.

### Tools

| Tool | Purpose |
|---|---|
| `matsne_suggest` | Fast keyword autocomplete |
| `matsne_search` | Filtered search (date, type, organ, pagination) |
| `matsne_get_document` | Full document: meta + text + tree + comments (any version) |
| `matsne_get_article` | One article by citation, optional part, as in force on a given date |
| `matsne_list_versions` | All consolidated versions + amending acts |
| `matsne_compare_versions` | Article-level diff between two versions |
| `matsne_check_status` | In force or expired, latest amendments |
| `matsne_get_tree` | Document structure (articles, chapters) |
| `matsne_get_back_references` | Citing documents |
| `matsne_get_comments` | User comments |
| `matsne_get_linked` | Specific article text |
| `matsne_search_by_keyword` | Active documents by keyword |
| `matsne_has_today` | Check today's publications |
| `matsne_today_documents` | Today's documents |

### ⚖️ Court practice (new)

| Tool | Purpose |
|---|---|
| `court_search_decisions` | Common-court decisions search (case no, date, instance, court, article, text) |
| `court_get_decision` | Full decision text |
| `court_classifiers` | Filter dictionaries (instances, courts, categories, articles) |
| `court_verify_act` | Verify a court act by barcode + case number |
| `supreme_search` | Supreme Court decision search (chamber, judge, result, text) |
| `supreme_get_case` | Full Supreme Court decision text |
| `supreme_classifiers` | Result / appeal-type codes |
| `supreme_grand_chamber` | Grand Chamber decisions + norm interpretations (binding) |

Every court result carries `authority_level` (5=Grand Chamber → 2=first instance)
so agents weigh precedent correctly.

### 🏛️ Apex courts (new)

| Tool | Purpose |
|---|---|
| `constitutional_search_acts` | Constitutional Court acts search (name, number, text) |
| `constitutional_get_act` | Full Constitutional Court act text |
| `echr_search` | European Court of Human Rights (HUDOC) search |
| `echr_get_case` | Full ECHR decision text |

### 🏛️ Parliament — bills (new)

| Tool | Purpose |
|---|---|
| `parliament_search_bills` | Search bills — what is about to change in the law |
| `parliament_get_bill` | Bill history: hearings, votes, committees, files |

### 🔬 Legal research (new)

| Tool | Purpose |
|---|---|
| `verify_citation` | Checks a quote really appears in the cited law/decision (anti-hallucination) |
| `research_article` | An article + its court practice in one call, by authority |

### 📚 Supreme Court analytics, deadlines, state fees, PDFs (new)

| Tool | Purpose |
|---|---|
| `supreme_practice_search` / `_list` / `_get` / `_categories` | Practice generalizations, recommendations, important explanations, decision collections, journal |
| `deadline_calculate` / `deadline_get_rule` / `deadline_calculate_custom` / `deadline_list_procedures` / `deadline_holidays` | Procedural deadlines from the live norm (quoted, holiday-aware, with a warning) — never from memory |
| `fee_calculate` / `fee_get_rule` / `fee_list_types` | Court state fee from the live Civil Procedure Code art. 39 (percent, minimum, ceilings for individuals/legal entities, magistrate, settlement) — with a warning |
| `pdf_get_text` / `pdf_search` | PDF text and search (legacy AcadNusx/LitNusx fonts repaired automatically) |
| `court_search_article` | Common-court decisions citing one article (ecd: 2018–2020) |

### 🗂️ Public registries (new)

| Tool | Purpose |
|---|---|
| `registry_company_search` / `_participations` / `_applications` | Company registry: entity by ID code or name (status), founders/partners, filings (liquidation, insolvency, share pledge) |
| `registry_property_parcel` / `registry_property_applications` | Real estate by cadastral code: owners, area, extract PDFs; application book |
| `registry_liens` | Seizures, prohibitions, tax liens (with the basis act, e.g. a court ruling) |
| `registry_movable_pledges` / `_get` | Pledges and leasing of movables / intangible assets |
| `registry_lawyer_search` / `registry_notary_search` | Bar members list, notary bureaus |
| `registry_procurement_list` / `_search` / `_get` | Procurement black, white and warned supplier lists |
| `companyinfo_company` / `_company_search` | companyinfo.ge (Transparency International): full company card — directors/partners and their history, shares, sanctions, blacklists, declarations, financial reports |
| `companyinfo_people_search` / `companyinfo_person` | A person's business roles by name or personal number (namesakes flagged; personal number used to cross-check other registries) |
| `geostat_search` / `geostat_company` / `_activity_codes` / `_regions` | Geostat business register: activity, size, addresses, head, history; filters by activity/region |

Open (no-CAPTCHA) sources only; search by ID code, personal number, cadastral code — and people by name too (companyinfo, business roles only, namesakes flagged); every result carries a personal-data warning.

### 🏢 Administrative bodies (new)

| Tool | Purpose |
|---|---|
| `tax_dispute_search` / `_get` | Ministry of Finance Dispute Resolution Council decisions (full text) |
| `rs_dispute_search` / `_get` | Revenue Service dispute council |
| `tax_guidance_search` / `_get` | Public rulings, situational guides |
| `competition_search` | Competition and Consumer Protection Agency |
| `ombudsman_search` / `_get` | Public Defender recommendations and proposals |
| `procurement_dispute_search` / `_get` | State procurement Dispute Resolution Council |
| `bar_ethics_search` | Bar Association ethics commission |

**Prompts:** `legal_memo`, `case_analysis` (IRAC), `law_change_review`.

### Quick install

```sh
pip install uv  # or install from https://docs.astral.sh/uv/
git clone https://github.com/sitechfromgeorgia/matsne-gov-ge-mcp
cd matsne-gov-ge-mcp
uv run mcp_server.py
```

Then add to Claude Desktop:

```json
{
  "mcpServers": {
    "matsne-ge": {
      "command": "uv",
      "args": ["--directory", "/path/to/matsne-gov-ge-mcp", "run", "mcp_server.py"]
    }
  }
}
```

### Example prompts

- _"Search for today's published documents on matsne.gov.ge"_
- _"Find laws about personal data published in the last month"_
- _"Get full information on document #6840140 and summarize it"_
- _"What did Article 361 of the Civil Code say on 1 January 2010?"_

---

## 📄 License

[MIT](LICENSE) — use freely, share, improve.

---

<p align="center">
  <strong><code>🇬🇪 შექმნილია სიყვარულით ბათუმიდან, საქართველო</code></strong><br>
  <strong><code>🇬🇪 Made with love from Batumi, Georgia</code></strong><br>
  <br>
  <sub>If this tool helps you, star it on GitHub ⭐ — it means a lot to a small dev from Batumi</sub>
</p>
