# Lipa za uporabnike sistema Windows

[Nazaj na README](../README.md) · [English](windows.en.md)

**Lipa trenutno nima izvorne različice za Windows.** Povezovalnik z namiznim Codexom
uporablja vtičnice Unix, preverjanje lastništva datotek in upravljanje procesov POSIX.
Namestitev Pythona ali uporaba Git Bash tega ne odpravi.

Spodaj je **predlagana, eksperimentalna pot prek WSL2 in WSLg**. V tem projektu še ni
bila preverjena s celovitim preizkusom. Ne gre za potrjeno podporo OpenAI za izvajanje
namizne aplikacije v WSLg. Če želiš uporabiti že delujočo namestitev na Linuxu, glej
[dostop prek SSH](#druga-možnost-windows-kot-odjemalec-za-linux).

## Kaj mora teči kje

| Del | Okolje |
| --- | --- |
| Lipa, Python in `codex` CLI | Ista distribucija WSL2 in isti uporabnik Linux. |
| Namizna aplikacija Codex/ChatGPT | **Različica za Linux znotraj te iste distribucije WSL2**, odprta prek WSLg. |
| Spletni vmesnik in mikrofon | Chrome v sistemu Windows, odprt na `http://127.0.0.1:8765`. |
| Orodja in naloge | Okolje Linux znotraj WSL2; to ni povezava do namizja Windows. |

Namizna aplikacija, nameščena samo v sistemu Windows, ne zagotovi vtičnic in datotek,
ki jih išče trenutni povezovalnik. Tudi izbira okolja WSL v aplikaciji Windows sama
po sebi ne pomeni združljivosti s tem povezovalnikom.

## 1. Pripravi WSL2 in grafično okolje WSLg

Potrebuješ Windows 11 ali Windows 10 z gradnjo 19044 ali novejšo ter podporo za
virtualizacijo. Zahteve za grafične aplikacije opisuje
[Microsoftova dokumentacija WSLg](https://learn.microsoft.com/en-us/windows/wsl/tutorials/gui-apps).

V **PowerShellu s skrbniškimi pravicami** zaženi:

```powershell
wsl --install -d Ubuntu-24.04
```

Po potrebi ponovno zaženi računalnik, odpri Ubuntu in ustvari uporabnika Linux.
Nato v PowerShellu preveri namestitev:

```powershell
wsl --update
wsl --list --verbose
```

Pri Ubuntu mora biti navedena različica **2**. Za obstoječo namestitev WSL1 je ukaz
za pretvorbo `wsl --set-version Ubuntu-24.04 2`. Ime mora ustrezati izpisu seznama.
Podrobnosti: [namestitev WSL](https://learn.microsoft.com/en-us/windows/wsl/install).

## 2. Namesti namizno aplikacijo in CLI znotraj Ubuntuja

V terminalu **Ubuntu**, ne v PowerShellu:

```bash
sudo apt update
sudo apt install git python3 python3-venv ca-certificates
uname -m
```

Z [uradne strani za Linux](https://learn.chatgpt.com/docs/linux/linux-app) prenesi
paket `.deb` za svojo arhitekturo in ga shrani v datotečni sistem Ubuntuja. Za x64
v mapi s prenesenim paketom zaženi:

```bash
sudo apt install ./chatgpt_amd64.deb
chatgpt
```

Za ARM64 uporabi datoteko `chatgpt_arm64.deb`. Ime prilagodi dejansko preneseni datoteki.
Okno aplikacije mora biti vidno prek WSLg. Prijavi se z računom ChatGPT in ga pusti odprtega.
Če se grafična aplikacija ne zažene, ta pot še ni uporabna; najprej odpravi težavo WSLg.

Znotraj Ubuntuja potrebuješ tudi Linuxov `codex` v `PATH`. Namestitev CLI je opisana
v [uradnih navodilih](https://learn.chatgpt.com/docs/cli). Nato preveri:

```bash
command -v codex
codex --version
codex login
```

Ukaz ne sme kazati na različico Windows v `/mnt/c/...` ali na `codex.exe`.
Prijava CLI mora biti izvedena znotraj Ubuntuja. Prijava samo v aplikacijo Windows ne zadošča.

## 3. Namesti in zaženi Lipo

V istem terminalu Ubuntu:

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/StrajnarFilip/lipa.git
cd lipa
./setup.sh
./run.sh
```

Projekt in `.venv` naj bosta v domači mapi Linux, kot v primeru zgoraj. Privzete poti
`/usr/lib/chatgpt/resources`, `~/.codex/ipc/ipc.sock` in `/tmp/codex-app-tools/` se morajo
nanašati na aplikacijo Linux v isti distribuciji. Če je aplikacija drugje, uporabi
[nastavitve povezave iz README](../README.md#nastavitve).

## 4. Odpri vmesnik v sistemu Windows

V **Chromu v sistemu Windows** odpri [http://127.0.0.1:8765](http://127.0.0.1:8765),
izberi **Začni pogovor** in dovoli mikrofon. Lokalna aplikacija v WSL je iz sistema
Windows običajno dosegljiva prek `localhost`; podrobnosti opisuje
[Microsoftova dokumentacija omrežja WSL](https://learn.microsoft.com/en-us/windows/wsl/networking).

Mikrofon uporablja brskalnik Windows, zato ga ni treba posredovati aplikaciji Linux.
Namizne zahteve za dovoljenja potrjuj v **oknu aplikacije Linux prek WSLg**. Povezava
**Odpri nalogo v Codex** lahko sproži obravnavo `codex://` v sistemu Windows; če odpre
napačno aplikacijo ali ne deluje, ročno izberi **Lipa desktop worker** v aplikaciji Linux.

Pri naslednjem zagonu odpri Ubuntu in namizno aplikacijo Linux, nato v drugem terminalu:

```bash
cd ~/projects/lipa
./run.sh
```

## Preverjanje in pogoste težave

Najprej poskusi tipkano vprašanje, nato glasovni pogovor. Preveri, da Lipa pokaže
povezan namizni Codex in pri izvedeni nalogi dejanski model Sol `xhigh`.

| Težava | Kaj preveriti |
| --- | --- |
| Grafična aplikacija Linux se ne odpre | Različico WSL2, posodobitev WSL in grafično podporo po navodilih WSLg. |
| Lipa ne najde namizne aplikacije ali vtičnic | Aplikacija Linux mora teči v isti distribuciji in pod istim uporabnikom kot Lipa. Namestitev Windows ne zadošča. |
| Viri so v `C:\...` ali CLI kaže v `/mnt/c/...` | Preveri, ali uporabljaš namestitvi aplikacije in CLI za Linux. |
| `127.0.0.1:8765` ni dosegljiv | Preveri, ali `./run.sh` še teče in ali vrata niso zasedena. Preveri pravila WSL, VPN in lokalnega požarnega zidu. |
| Spletna stran deluje, mikrofon pa ne | Dovoli mikrofon v Chromu in v nastavitvah zasebnosti sistema Windows. |
| Napaka zasebnega protokola ali neuspešna glasovna povezava | Namestitev še ni potrjeno združljiva. Zabeleži različico CLI in napako brez prijavnih žetonov. |

Za diagnostiko dostopnosti v terminalu Ubuntu lahko uporabiš:

```bash
python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8765/api/status').status)"
```

Odgovor `200` potrdi le dostopnost strežnika, ne pa delovanja govora ali namiznih orodij.

## Druga možnost: Windows kot odjemalec za Linux

Če Lipa že deluje na ločenem računalniku z Linuxom, naj tam ostaneta odprta Lipa in
namizni Codex. V PowerShellu z nameščenim odjemalcem OpenSSH vzpostavi tunel; zamenjaj
uporabniško ime in naslov gostitelja:

```powershell
ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 uporabnik@linux-gostitelj
```

Nato odpri `http://127.0.0.1:8765` v Chromu v sistemu Windows. Mikrofon in predvajanje
sta v sistemu Windows, naloge in dovoljenja pa ostanejo na računalniku Linux. Za
odobritve potrebuješ dostop do njegovega grafičnega namizja. Povezava `codex://` v
brskalniku Windows ne odpre samodejno pogovora na oddaljenem računalniku.

Lipa lahko ostane vezana na `127.0.0.1`; za ta dostop ji ni treba odpreti javnih vrat.
Za uporabo te možnosti ne potrebuješ WSL.
