# Lipa

**Glasovni pomočnik za pogovor v slovenščini in temeljito delo v ozadju s Codexom.**

Slovenščina · [English](README.en.md) · [Apache 2.0](LICENSE) · [Poročilo o preverjanju](VALIDATION.md)

Lipa poveže izvorni glasovni vmesnik Codex z ločenim pogovorom v namizni aplikaciji.
Ti govoriš, Codex pa lahko medtem raziskuje splet, uporablja brskalnik, izvaja ukaze
in ureja datoteke. Za zahtevnejše naloge uporablja **GPT-6.1 Sol z nastavitvijo `xhigh`**.
Govor lahko prekineš in dodaš popravek, ne da bi s tem ustavil delo v ozadju.

Uporablja obstoječo prijavo ChatGPT. Ne potrebuje ločenih ključev API za govor ali
sklepanje in ob napaki ne preklopi na drugega plačljivega ponudnika.

> **Eksperimentalna izvedba za Linux.** Preverjena je bila na Fedori z
> `codex-cli 0.164.0-alpha.11.1`. Glasovni protokol in povezava z namizno aplikacijo
> sta eksperimentalna; po posodobitvi Codexa bo morda treba prilagoditi povezovalnik.

## Kaj omogoča

- Govorni vnos in odgovore v slovenščini, sproten prepis ter tipkane popravke.
- Temeljit razmislek v ločenem namiznem pogovoru, ki ohrani kontekst med nalogami.
- Uporabo obstoječih orodij Codex: brskalnika, spletnega iskanja, ukazov, datotek in vtičnikov.
- Prikaz napredka, uporabljenih orodij ter dejanskega modela in ravni razmisleka.
- Ločena dejanja za utišanje mikrofona, prekinitev govora in preklic naloge.
- Lokalno shranjevanje pogovora ter ponovno povezavo brez samodejnega ponavljanja nalog.

## Kaj potrebuješ

| Zahteva | Podrobnosti |
| --- | --- |
| Linux z namizno aplikacijo Codex/ChatGPT | Privzete poti ustrezajo preverjeni namestitvi na Fedori. Aplikacija mora biti odprta in prijavljena. |
| Dostop prek računa ChatGPT | Račun mora omogočati izvorni glasovni vmesnik in `gpt-6.1-sol` z ravnijo `xhigh`. Lipa ne izbere nadomestnega modela. |
| Ukaz `codex` v `PATH` | Potreben je za glasovni proces `app-server`. Sama namestitev CLI ne zadošča za namizna orodja. |
| Python 3.11 ali novejši | Z moduloma `venv` in `ensurepip`. |
| Brskalnik z WebRTC in mikrofon | Preverjeno s Chromom. Za prvi preizkus so priporočljive slušalke. |

Ločena namestitev Node.js ali gradnja uporabniškega vmesnika za zagon nista potrebni.
Lokalni strežnik uporablja FastAPI/Uvicorn, vmesnik pa navaden HTML, CSS in JavaScript.

## Hiter začetek

1. Odpri namizno aplikacijo Codex/ChatGPT in se prijavi.
2. V terminalu preveri CLI in njegovo prijavo:

   ```bash
   codex --version
   codex login
   ```

   Navodila za namestitev so v [dokumentaciji Codex CLI](https://learn.chatgpt.com/docs/cli).

3. Prenesi in zaženi Lipo:

   ```bash
   git clone https://github.com/StrajnarFilip/lipa.git
   cd lipa
   ./setup.sh
   ./run.sh
   ```

4. Odpri **[http://127.0.0.1:8765](http://127.0.0.1:8765)**, izberi **Začni pogovor**
   in dovoli uporabo mikrofona. Lahko tudi takoj napišeš sporočilo.

Ob prvi vsebinski zahtevi Lipa ustvari pogovor **Lipa desktop worker** v namizni
aplikaciji. Naslednje naloge pošilja v isti pogovor. Gumb **Odpri nalogo v Codex**
odpre njegova orodja, rezultate in zahteve za dovoljenja.

Pri naslednjem zagonu zadošča `./run.sh`. Namizna aplikacija mora ostati odprta.
Druga vrata lahko izbereš z `./run.sh --port 8766`.

## Pogovor in delo v ozadju

| Dejanje ali izgovorjeni ukaz | Kaj se zgodi |
| --- | --- |
| **Začni pogovor** | Vzpostavi glasovno povezavo in vključi mikrofon. |
| **Premisli temeljito …** | Izrecno pošlje zahtevo namiznemu modelu Sol `xhigh`; med delom usmeri aktivno nalogo. Enako omogoča gumb ob polju za sporočilo. |
| **Popravek: …** | Doda spremembo aktivni nalogi. Tudi tipkano sporočilo med delom usmeri trenutno nalogo. |
| Prekinitev govora | Ustavi predvajanje odgovora; delo v ozadju ostane aktivno. |
| Utišanje mikrofona | Ustavi govorni vnos; predvajanje in delo v ozadju ostaneta aktivna. |
| **Nadaljuj** | Glasovnemu pomočniku naroči nadaljevanje prekinjenega odgovora. |
| **Prekliči nalogo** | Zahteva prekinitev dela in čiščenje njegovih terminalskih procesov; vmesnik počaka na potrditev. |
| **Končaj pogovor** | Konča glasovno sejo, aktivna namizna naloga pa lahko nadaljuje delo. |
| **Ponovno poveži** | Obnovi spremljanje namizne naloge; zahteve ne pošlje ponovno. |

V polju za sporočilo **Enter** pošlje besedilo, **Shift + Enter** pa doda novo vrstico.
Običajen kratek pogovor lahko teče tudi med zahtevnejšim delom. Za jasno usmeritev
aktivne naloge začni s »Popravek« ali »Premisli temeljito«.

Primeri:

```text
Premisli temeljito. Na Amazon.de poišči tri USB-C priklopne postaje do 100 evrov.
Primerjaj priključke in dodaj povezave do izdelkov.

Popravek: priklopna postaja mora podpirati dva zunanja zaslona.

Premisli temeljito. Preglej ta projekt, poišči vzrok napake in preveri popravek.
```

Dovoljenja za spletna mesta, prijave in odobritve dejanj ureja **namizni Codex**.
Če Lipa pokaže **Čakam tvojo potrditev v Codex**, odpri namizno nalogo in preglej
zahtevo. Dostop do spletnega mesta ne odpravi morebitne prijave ali preverjanja CAPTCHA.
Preklic ne razveljavi dejanj, ki so bila že izvedena.

## Kako deluje

| Del | Vloga |
| --- | --- |
| Brskalniški vmesnik | Mikrofon, predvajanje zvoka, prepis, tipkanje in upravljanje nalog. |
| Izvorni Codex Voice | Prepoznavanje govora, sproten pogovor in izgovarjanje odgovorov prek WebRTC. |
| Lokalni strežnik Lipa | Združevanje govornih odlomkov, usmerjanje zahtev, stanje seje in dostava potrjenih rezultatov. |
| Namizni pogovor Codex | Sol `xhigh`, orodja, dovoljenja in izvajanje dela. |

Glasovna seja ima lasten proces `app-server`; delo prevzame ločen pogovor v namizni
aplikaciji. Lipa uporablja njen priloženi `codex-app-tools` MCP in zasebni protokol
za spremljanje, usmerjanje in prekinjanje pogovorov. Orodij za upravljanje brskalnika
ne izvaja znova v lastni kodi.

## Nastavitve

Privzete nastavitve so namenjene enemu uporabniku in eni aktivni glasovni seji.
Spremenljivke okolja nastavi pred zagonom; datoteke `.env` se ne nalagajo samodejno.

| Spremenljivka | Privzeto | Namen |
| --- | --- | --- |
| `VOICE_STATE_DIR` | `.state` v mapi projekta | Mapa s shranjenim stanjem Lipe. |
| `VOICE_CODEX_RESOURCES` | `/usr/lib/chatgpt/resources` | Mapa namizne aplikacije s priloženim Node in `codex-app-tools`. |
| `VOICE_DESKTOP_IPC` | `$CODEX_HOME/ipc/ipc.sock` oziroma `~/.codex/ipc/ipc.sock` | Lokalna vtičnica za spremljanje in upravljanje pogovora. |
| `VOICE_DESKTOP_PIPE` | Vrednost iz aplikacije ali edina zasebna vtičnica v `/tmp/codex-app-tools/` | Izrecna izbira povezave z namiznimi orodji. |
| `VOICE_PYTHON` | `python3` | Tolmač za ustvarjanje `.venv`; velja pri `./setup.sh`. |

Primer ločenega pogovora in drugih vrat:

```bash
VOICE_STATE_DIR=.state/drugi-pogovor ./run.sh --port 8766
```

Povezovalnik sprejme le zasebne vtičnice v lasti trenutnega uporabnika. Če zazna več
možnih povezav, zahteva izrecno pot `VOICE_DESKTOP_PIPE`. Iste namizne naloge ne
upravljaj hkrati iz več primerkov Lipe.

## Zasebnost, omejitve in poraba

- Spletni strežnik posluša na `127.0.0.1`. **Sklepanje in obdelava govora uporabljata
  storitve OpenAI; Lipa ni rešitev brez povezave.**
- `.state/session.json` vsebuje prepis, identifikatorja glasovnega in namiznega pogovora
  ter zadnje stanje naloge. Lipa vanj ne shranjuje prijavnih žetonov ali zvoka.
  Codex ločeno hrani lastno zgodovino. Lokalno stanje in preizkusni posnetki so izključeni iz Gita.
- Po prekinitvi povezave izid naloge označi kot nepotrjen. Ob ponovni povezavi preveri
  stanje obstoječe naloge in je samodejno ne izvede znova.
- Uporablja prijavo in omejitve računa ChatGPT. Ne poveča razpoložljive porabe, ne kupuje
  dobroimetja in ne preklopi na obračunavanje s ključem API. Morebitno dobroimetje na
  računu ostaja odvisno od njegovih nastavitev.
- Izgovarjava in prepoznavanje slovenščine sta odvisna od izvornega glasovnega vmesnika.
  Še vedno so možni napačen prepis, menjava jezika ali napačna izgovarjava.
- Daljši premori sredi stavka lahko sprožijo prezgodnje razmišljanje. Nadaljevanje se
  poskuša dodati kot popravek. Natančna poravnava slišanih besed z besedilom ni na voljo.
- Trenutna izvedba omogoča brskalniška orodja in ukaze, ne pa splošnega upravljanja
  grafičnih aplikacij namizja Linux.

## Odpravljanje težav

| Težava | Kaj preveriti |
| --- | --- |
| Namizni Codex ni dosegljiv | Odpri in prijavi namizno aplikacijo, nato izberi **Ponovno poveži**. Preveri poti `VOICE_CODEX_RESOURCES`, `VOICE_DESKTOP_IPC` in `VOICE_DESKTOP_PIPE`. |
| Ukaz `codex` manjka ali prijava ni veljavna | Preveri `codex --version`, namestitev CLI in `codex login`. Prijava samo s ključem API ni podprta. |
| Model Sol ali raven `xhigh` nista na voljo | Preveri dostop računa v Codexu. Lipa namenoma ne izbere drugega modela. |
| Mikrofon je zavrnjen ali ni zvoka | Dovoli mikrofon za lokalno stran, preveri izbrano napravo in ponovno začni pogovor. Tipkanje ostane na voljo. |
| Naloga čaka na dovoljenje | Izberi **Odpri nalogo v Codex** in odgovori na poziv v namizni aplikaciji. |
| Dosežena omejitev porabe | Preveri omejitve računa v Codexu. Lipa nima rezervnega plačljivega ponudnika. |
| Po posodobitvi se protokol ne ujema | Preveri sporočilo o napaki in združljivost namizne različice; povezovalnik bo morda potreboval prilagoditev. |
| Ustvarjanje `.venv` ne uspe | Namesti podporo za `venv`/`ensurepip` ali uporabi `VOICE_PYTHON=/pot/do/python3 ./setup.sh`. |

Ob nejasnem izidu pošiljanja najprej poglej namizni pogovor, preden nalogo pošlješ še enkrat.

## Razvoj in preizkusi

Samodejni preizkusi uporabljajo nadomestne protokole in ne porabljajo omejitev ChatGPT:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Živi preizkus uporablja pravi glasovni vmesnik in Codex ter **porablja omejitve računa**.
Potrebuje Node.js, Playwright in nameščen Chrome/Chromium. Zaženi ga z ločenim stanjem:

```bash
# Prvi terminal
VOICE_STATE_DIR=test-results/live-state ./run.sh --port 8766

# Drugi terminal, v mapi projekta
npm install --prefix test-results/browser-tools playwright
VOICE_URL=http://127.0.0.1:8766 \
PLAYWRIGHT_MODULE="$PWD/test-results/browser-tools/node_modules/playwright" \
CHROME_BIN=/pot/do/chrome \
node scripts/browser_acceptance.cjs
```

Ob prvem zagonu z novim stanjem preizkus ustvari ločen namizni pogovor. Uporablja
sintetični mikrofon ter preveri prepis, delegiranje, zvok in prikaz na manjšem zaslonu.
Ne uporablja fizičnega mikrofona. Poročila in posnetke zaslona shrani v `test-results/`.
Preverjene rezultate in omejitve preizkusov opisuje [VALIDATION.md](VALIDATION.md).

| Datoteka | Vloga |
| --- | --- |
| `voice_harness/desktop.py` | Povezava z namiznimi nalogami in njihovim lastnikom. |
| `voice_harness/rpc.py` | Glasovni proces Codex in protokol JSONL. |
| `voice_harness/native_voice.py` | Prilagodilnik za izvorni glasovni vmesnik. |
| `voice_harness/service.py` | Usmerjanje pogovora ter življenjski cikel nalog. |
| `voice_harness/app.py` | Lokalni HTTP API in dogodki WebSocket. |
| `voice_harness/static/` | Uporabniški vmesnik brez gradnje. |
| `tests/` | Samodejni preizkusi usmerjanja, stanja in protokolov. |

## Licenca

Copyright 2026 Filip Strajnar. Lipa je na voljo pod [licenco Apache 2.0](LICENSE).
Storitve OpenAI in priloženi deli njihove namizne aplikacije ostajajo predmet lastnih
pogojev in licenc; ta repozitorij jih ne vključuje.
