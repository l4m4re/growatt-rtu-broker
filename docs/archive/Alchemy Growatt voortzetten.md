> Historical conversation transcript imported on 2026-11-26 from the private
> `growatt-firmware` repository at source HEAD `abe097b` (originally committed
> as `ed0f0a6`). This evolving discussion is context, not current operating
> policy or independently verified evidence. Private LAN addresses and the
> stock-firmware MQTT password value were redacted in this archive copy. Use
> [`AGENTS.md`](../../AGENTS.md) and [`ROADMAP.md`](../ROADMAP.md) for current
> broker requirements and authorization boundaries.

---

**You:**

[Image omitted: the exported transcript contained a signed private attachment URL.]

Graag de Alchemy ZABA22 chat voortzetten als Alchemy Growatt. De truuk lijkt inderdaad te zijn om de working mode naar default te zetten.

* * *

**ChatGPT:**

Prima, we zetten **Alchemy ZABA22** hier voort onder de naam **Alchemy Growatt**.

En daarmee lijkt de oorzaak van het probleem inderdaad gevonden: de instelling **Working Mode = Multi Parallel** in plaats van **Default**.

In de eerdere registerscan zagen we dat de Growatt bij ongeveer 60% SOC vrijwel niets naar de ARK-batterij stuurde, terwijl het PV-overschot werd teruggeleverd. De overige batterijinstellingen leken dat gedrag niet te verklaren.

Nu, na het wijzigen van de working mode, zien we:

| Parameter | Waarde |
| --- | --- |
| PV-productie | 957 W |
| Huisverbruik | 615 W |
| Teruglevering | 344 W |
| Batterij laden | **372 W** |
| Batterij ontladen | 0 W |
| SOC | 61% |

**Het belangrijkste is dat de batterij weer daadwerkelijk laadt.** Dat is een sterke aanwijzing dat Multi Parallel de oorzaak was.

Er zit wel iets interessants in de metingen: 957 W PV minus 615 W huisverbruik is ongeveer 342 W, vrijwel exact de gemelde teruglevering van 344 W. De 372 W batterijlading komt daar nog bij. De energiebalans sluit dus niet als alle sensoren dezelfde vermogensstromen op hetzelfde moment weergeven.

Dat kan komen door verschillende meetmomenten, een andere definitie van *House Consumption* of de manier waarop Growatt de batterij- en netstromen rapporteert.

## Wat dit betekent voor ons firmwareonderzoek

We hebben nu een interessante nieuwe onderzoeksvraag:

**Welke registerwaarde selecteert Multi Parallel en hoe beïnvloedt die de energieroutering in de ZABA22/ZABA23-firmware?**

Dat sluit direct aan bij ons bestaande reverse-engineeringonderzoek naar de interactie tussen legacy-TOU, VPP, de meterconfiguratie en batterijregeling.

Mijn voorstel is om de registerscan van vóór de wijziging te vergelijken met een nieuwe scan na het instellen van Default. Daarmee kunnen we de gewijzigde registers identificeren en vervolgens in de firmware onderzoeken welke codepaden daarvan afhankelijk zijn.

Ik zou de huidige instelling voorlopig op **Default** laten staan en eerst controleren of de batterij ook bij hogere PV-productie normaal blijft laden.

Dit is in ieder geval een belangrijke stap: we hebben nu een reproduceerbare aanwijzing dat de working mode een bepalende rol speelt bij het laden van de ARK-batterij.

> Interactive content unavailable: MemoryCite

* * *

**You:**

register-scan-incl-working mode.zip

Nu is de vraag of deze setting gewijzigd is t.g.v. de problemen die we in kaart hebben gebracht rond winter mode en vpp gebruik, waarnaa ook de TOU tabel opnieuw geschreven moest worden. Zie [https://github.com/0xAHA/Growatt_ModbusTCP/issues/494](https://github.com/0xAHA/Growatt_ModbusTCP/issues/494)  - is uit de scans te achterhalen dat de operation mode inderdaad met/door dit experiment gewijzigd is?

* * *

**ChatGPT:**

## Alchemy Growatt — eerste bevinding uit de scans

**Ja, de scans tonen een duidelijke wijziging van de bedrijfsinstellingen tijdens de experimenten.** Maar de tijdlijn wijst voorlopig niet rechtstreeks naar het activeren van Winter Mode als oorzaak.

Ik heb de 50 registerscans in je ZIP chronologisch vergeleken, van 10 tot en met 22 november 2026.

Er valt iets belangrijks op: op **14 november tussen 23:10 en 23:53 Nederlandse tijd** veranderen twee instellingen, die vervolgens tot vandaag ongewijzigd blijven. Na het terugzetten van de working mode naar Default op 22 november keren beide terug naar hun oorspronkelijke waarden.

| Moment (CET) | `priority_mode` | `tl_xh_priority_mode` |
| --- | --- | --- |
| 10 nov. 11:30 | 0 | 0 |
| 13 nov. 23:43 | 0 | 0 |
| 14 nov. 23:10 | 0 | 0 |
| **14 nov. 23:53** | **1** | **2** |
| 15 nov. 10:20 | 1 | 2 |
| 22 nov. 11:51 | 1 | 2 |
| **22 nov. 12:10** | **0** | **0** |

Dit is een sterke temporele aanwijzing voor een verband met de operation-mode-instelling, maar we moeten nog vaststellen welke fysieke registers bij deze twee entiteiten horen en of ze rechtstreeks de portalinstelling Multi Parallel vertegenwoordigen.

## Het exacte omslagmoment

De wijziging blijkt bovendien zichtbaar in drie fysieke holding registers.

| Register | Vóór omslag | Na omslag | Na herstel vandaag |
| --- | --- | --- | --- |
| `H3018` | 0 | 2 | 0 |
| `H7261` | 0 | 2 | 0 |
| `H7271` | 21124 | 11110 | 21124 |

Deze drie registers hebben een bijzonder sterk patroon: de waarden van vóór de omslag zijn exact gelijk aan de waarden na het herstel van vandaag.

`H3018` is in de integratie geïdentificeerd als *Priority Mode*, met waarde 0 = Load First en 2 = Battery First. `H7261` en `H7271` zijn in de scan niet van een betekenis voorzien. We moeten dus nog onderzoeken of ze dezelfde configuratie weerspiegelen of een andere operationele status.

### Correlatie met issue #494

Uit de brokerregistraties in [issue #494](https://github.com/0xAHA/Growatt_ModbusTCP/issues/494?utm_source=chatgpt.com) blijkt het volgende:

1. **13 november, 23:01 CET — eerste Winter Mode-activatie**
  Shine schrijft `H30100=1` en de drie VPP SOC-registers. De daaropvolgende scans laten `H3018=0` zien.
2. **14 november, 23:01–23:03 CET — tweede Winter Mode-activatie**
  Shine schrijft opnieuw de Winter Mode-registers en vervolgens `H192=64`. Dit is de tweede capture die in issue #494 is beschreven.
3. **14 november, tussen 23:10 en 23:53 CET — configuratie verandert**
  Binnen dit venster veranderen `H3018`, `H7261` en `H7271`. De nieuwe waarden blijven vervolgens ruim een week aanwezig.
4. **22 november, tussen 11:51 en 12:10 CET — handmatig herstel**
  Na jouw wijziging naar Default staan alle drie de registers weer op hun oorspronkelijke waarden.

**De timing is opvallend:** de tweede Winter Mode-activatie vond vlak vóór de vastgestelde omslag plaats. Dat maakt een causaal verband aannemelijk, maar nog niet bewezen.

De eerste Winter Mode-activatie had deze omslag namelijk niet aantoonbaar veroorzaakt.

## Conclusie

Mijn voorlopige conclusie is dat **de wijziging waarschijnlijk tijdens de tweede Winter Mode/VPP-test is ontstaan**, en dat de scans daarvoor aanzienlijk sterker bewijs leveren dan alleen het herstel van de batterijlading vandaag.

Wat we nog niet kunnen vaststellen, is of:

- Winter Mode zelf een interne configuratiewijziging veroorzaakte;
- de Growatt-portal naast de geregistreerde VPP-writes nog een afzonderlijke wijziging uitvoerde;
- of een andere handeling tijdens hetzelfde tijdvenster de configuratie veranderde.

De bekende Winter Mode-write-sequentie bevat geen expliciete write naar `H3018`, `H7261` of `H7271`.

**Mijn voorstel voor de volgende stap in Alchemy Growatt:** laat Codex het bestaande volledige brokerlog van 14 november tussen 22:10 en 22:53 UTC onderzoeken, specifiek op writes naar deze drie registers en eventuele samenhang met `H192=64`.

Als er geen directe writes gevonden worden, is dat juist interessant. Dan zouden we in de ZABA22-firmware kunnen onderzoeken of de Winter Mode/VPP-afhandeling indirect `H3018` of de andere twee registers wijzigt.

Dat zou een veel fundamentelere verklaring kunnen opleveren voor het probleem dan alleen een verkeerde instelling in de portal.

* * *

**You:**

Het lijkt er op dat vpp gebruik op mijn inverter niet alleen niet werkt, maar ook side effecten heeft. Weet alleen niet of het zinvol is richting growatt nog een follow up te doen, maar wellicht is het wel zinvol op de issue een reply te doen om e.e.a. te documenteren.

* * *

**ChatGPT:**

Ik denk dat het zeker zinvol is om dit aan **[issue #494](https://github.com/0xAHA/Growatt_ModbusTCP/issues/494?utm_source=chatgpt.com)** toe te voegen. Richting Growatt Support zou ik voorlopig terughoudender zijn.

Er ontstaat namelijk een steeds duidelijker patroon:

1. Winter Mode activeert VPP (`H30100=1`), maar de SOC-registers zijn niet correct terug te lezen.
2. TOU-wijzigingen via de portal worden niet betrouwbaar verwerkt. Na uitschakelen van Winter Mode moest de TOU-tabel handmatig worden hersteld.
3. Nu blijkt ook een blijvende wijziging van de bedrijfsconfiguratie te zijn opgetreden, waarbij het PV-overschot niet meer normaal naar de batterij werd geleid.
4. Het terugzetten van de working mode naar Default heeft het laden van de batterij hersteld.

Dat laatste is belangrijk, omdat de configuratiewijziging **ook na het uitschakelen van VPP bleef bestaan**.

Ik heb de scanwaarden nogmaals gecontroleerd. De wijziging ontstond tussen 14 november 22:10 en 22:53 UTC, dus binnen 52 minuten na de tweede Winter Mode-write-sequentie. Het exacte mechanisme is echter nog onbekend.

### Voorstel voor reactie op issue #494

## Additional observation: possible persistent side effect of Winter Mode / VPP

I have encountered another problem that may be related to the Winter Mode/VPP experiments described above.

On November 22, I noticed that my MIN 6000TL-XH was exporting surplus PV power to the grid instead of charging the ARK battery, despite the battery SOC being around 60% and the battery not being at its configured charging limit.

After some investigation, I found that the inverter's **Working Mode** was set to **Multi Parallel** rather than **Default**.

Changing this setting back to Default restored battery charging.

### Register scan evidence

I compared the historical full register scans and found an interesting correlation with the second Winter Mode activation on November 14.

| Time (CET) | H3018 | H7261 | H7271 |
|---|---:|---:|---:|
| Nov 14, 23:10 | 0 | 0 | 21124 |
| Nov 14, 23:53 | 2 | 2 | 11110 |
| Nov 22, 11:51 | 2 | 2 | 11110 |
| Nov 22, 12:10, after restoring Default | 0 | 0 | 21124 |

The integration identifies H3018 as the TL-XH priority mode register (0 = Load First, 2 = Battery First). The precise meaning of H7261 and H7271 remains unknown.

The important observation is that all three registers returned to their original values after changing Working Mode back to Default.

### Timing relative to Winter Mode

The second Winter Mode activation was captured on November 14 at approximately 22:01 UTC (23:01 CET):

- H30100 = 1
- H30405 = 30
- H30406 = 30
- H30475 = 10
- H192 = 64 (approximately one minute later)

The register configuration changed between the scans at 22:10 and 22:53 UTC.

Interestingly, the earlier Winter Mode activation on November 13 did not produce the same persistent configuration change.

### Interpretation

This does **not yet prove** that Winter Mode or VPP directly caused the change. The known Winter Mode write sequence does not explicitly include H3018, H7261 or H7271, and it is not yet established how these registers relate to the portal's Multi Parallel setting.

However, the timing is suspicious, especially considering the other problems encountered during these experiments:

- VPP SOC registers acknowledge writes but read back as zero.
- TOU settings submitted through the portal are not reliably applied while Winter Mode is active.
- The legacy TOU table required manual cleanup after the experiments.
- The altered operating configuration apparently persisted after VPP was disabled.

It is therefore possible that the Winter Mode/VPP control path causes additional configuration changes that are not fully restored when switching back to legacy operation.

I have not attempted to reproduce this problem, as doing so could interfere with normal battery operation.

The next useful step would be to examine the broker write capture around November 14, 22:10–22:53 UTC, and investigate whether the ZABA22 firmware contains an internal control path capable of changing these settings indirectly.

For now, the inverter is back in its normal operating configuration and the ARK battery is charging again.

**Register scan archive:** available, including scans before and after the configuration change and its restoration.

### Zou ik Growatt opnieuw benaderen?

Voorlopig niet met een nieuw uitgebreid technisch verslag. In issue #494 staat al dat Growatt R&D heeft aangegeven dat TOU vóór Winter Mode moet worden ingesteld, en dat sommige instellingen na activering ongeldig kunnen worden. Dat suggereert dat er in ieder geval bekende interacties tussen beide functies bestaan.

Wel zou ik deze bevinding bewaren als aanvulling voor Growatt, vooral als we uit de brokerlogs of ZABA22-firmware kunnen aantonen dat de firmware zelf de operationele configuratie verandert.

**Mijn voorkeur is daarom: eerst documenteren op GitHub, daarna de oorzaak met Alchemy onderzoeken.** Als we een concreet intern codepad of een afzonderlijke onbedoelde Modbus-write kunnen aanwijzen, hebben we een aanzienlijk sterker verhaal richting Growatt R&D.

En minstens zo belangrijk: dit onderzoek hoeft niet te betekenen dat we Winter Mode opnieuw op je live inverter moeten activeren. We hebben inmiddels voldoende historische scans en brokerregistraties om eerst offline verder te zoeken.

* * *

**You:**

Ik heb dus vanaf de portal alle batterij settings nog een keer geschreven, inclusief electric meter setting en als laatste die mode.

* * *

**ChatGPT:**

Ah, dat is een belangrijke nuance voor de analyse!

Je hebt dus niet uitsluitend de **Working Mode van Multi Parallel naar Default** gewijzigd, maar via de Growatt-portal achtereenvolgens alle batterijinstellingen opnieuw geschreven, inclusief de **Electric Meter-configuratie**, en als laatste de Working Mode.

Daarmee moeten we twee zaken uit elkaar houden:

1. **De historische configuratiewijziging:** de scans tonen dat op 14 november tussen 23:10 en 23:53 CET meerdere registers veranderden, kort na de tweede Winter Mode/VPP-activatie.
2. **Het herstel op 22 november:** na het opnieuw schrijven van alle batterijinstellingen begon de ARK weer te laden. We kunnen niet met zekerheid zeggen welke afzonderlijke wijziging daarvoor verantwoordelijk was.

Ook de wijziging van `H3018` van 2 naar 0 kan dus het gevolg zijn van het opnieuw schrijven van de batterijprioriteit, en hoeft niet rechtstreeks samen te hangen met de Working Mode-instelling.

**Dat verandert de conclusie voor issue #494.** We hebben bewijs voor een blijvende configuratieverandering die temporeel samenvalt met de VPP-experimenten, maar nog geen bewijs dat Winter Mode de instelling Multi Parallel heeft geactiveerd.

Ik zou daarom eerst in de brokerlogs zoeken naar de oorspronkelijke writes rond 14 november én de herstelwrites van vandaag. Vooral die laatste kunnen helpen vaststellen welke fysieke registers door de portal worden geschreven bij het veranderen van Working Mode.

Als we dat kunnen isoleren, wordt het mogelijk veel nauwkeuriger vast te stellen of de oorspronkelijke afwijking werkelijk door Winter Mode/VPP is veroorzaakt.

De eerder opgestelde GitHub-reactie zou ik **in deze vorm nog niet plaatsen**. Die legt een te direct verband tussen het terugzetten van Working Mode en het herstel van de batterijlading.

* * *

**You:**

[Image omitted: the exported transcript contained a signed private attachment URL.]

VPP.communication.protocol-V2.05-EN(2).pdf

AppNote_Growatt_WIT-Modbus-RTU-Protocol-II-V1.39-English(1).pdf

Het lijkt me vrij duidelijk dat de oorzaak van de waargenomen problemen enkel en alleen in H3018 zit. De batterij settings kunnen in de laatste twee scans vergeleken worden en zijn als het goed is niet gewijzigd.

* * *

**ChatGPT:**

Je hebt gelijk. Met de protocoldefinitie erbij wordt de conclusie aanzienlijk sterker.

In de Growatt Modbus RTU Protocol V1.39 staat expliciet:

| H3018 — WorkMode | Betekenis |
| --- | --- |
| 0 | Default |
| 1 | System Retrofit |
| 2 | Multi-Parallel |

Dit is dus niet de batterijprioriteit, zoals ik eerder aannam, maar de **operationele configuratie van de inverter zelf**.

## Vergelijking van de laatste twee scans

Ik heb de twee CSV's uit je ZIP rechtstreeks vergeleken.

22 november 2026, 11:51 versus 12:10 CET

| Instelling/register | Voor | Na |
| --- | --- | --- |
| **H3018 — WorkMode** | **2** | **0** |
| H3036 — Grid First discharge power | 20 | 20 |
| H3037 — Grid First stop SOC | 10 | 10 |
| H3047 — Battery First charge power | 100 | 100 |
| H3049 — AC charging | 0 | 0 |
| H3067 — On-grid stop SOC | 60 | 60 |
| H30100 — VPP authority | 0 | 0 |
| H30405 / H30406 / H30475 | 0 | 0 |
| Battery First charge stop SOC | 90% | 90% |

**De relevante batterijinstellingen zijn inderdaad identiek gebleven.**

Daarnaast zijn er twee interessante veranderingen:

- `H7261`: 2 → 0
- `H7271`: 11110 → 21124

De betekenis daarvan is nog niet vastgesteld. Ze kunnen een afgeleide of interne representatie van de operationele configuratie zijn.

## Wat kunnen we nu concluderen?

De waarnemingen passen uitstekend bij de hypothese dat **H3018=2 de oorzaak van het laadprobleem was**.

We hebben nu:

1. Een expliciete protocoldefinitie die `H3018=2` als Multi-Parallel identificeert.
2. Historische scans die aantonen dat H3018 op 14 november van 0 naar 2 veranderde, kort na het tweede Winter Mode-experiment.
3. Een langdurig aanwezige afwijkende WorkMode, terwijl VPP inmiddels weer uitgeschakeld was.
4. Herstel van het laden nadat H3018 naar 0 was teruggezet, zonder wijzigingen in de gecontroleerde batterijinstellingen.

Dit is sterke aanwijzing voor een **persistent side effect van de Winter Mode/VPP-experimenten**.

Er blijft één onderscheid: dat H3018=2 het laadprobleem veroorzaakte, is inmiddels zeer aannemelijk. Dat de VPP-code zelf H3018 op 2 heeft gezet, moeten we nog bewijzen.

Daarvoor zou ik in de ZABA22-firmware zoeken naar alle schrijfpaden naar de interne opslag van H3018, met bijzondere aandacht voor de activering van `H30100` en de verwerking van `H192`.

Ik vind dit inmiddels een behoorlijk relevante bevinding voor issue #494. Het gaat mogelijk niet alleen om VPP-functionaliteit die niet goed werkt, maar om een **wijziging van een fundamentele inverterinstelling die na het uitschakelen van VPP blijft bestaan**.

Dat verdient beslist documentatie, ook als we het exacte mechanisme nog niet hebben gevonden.

* * *

**You:**

growatt_all_write_capture_20261113_154655_until_20261115.zip

Het broker log zou dan uitsluitsel moeten geven of H3018 geschreven is en, zo ja, vanaf welke client of dat hij niet geschreven is en dan dus kennelijk intern in de inverter is overschreven t.g.v. het vpp mode experiment en dus een vpp bug is.

* * *

**ChatGPT:**

## Alchemy Growatt — oorzaak van H3018-wijziging gevonden

**Het brokerlog geeft uitsluitsel: H3018 is daadwerkelijk via Modbus geschreven. De wijziging is dus niet uitsluitend intern door de inverter ontstaan.**

In het aangeleverde log `growatt_all_write_capture_20261113_154655_until_20261115.zip` staat precies één write naar H3018:

| Eigenschap | Waarde |
| --- | --- |
| Tijdstip (UTC) | 14 november 2026, 22:43:30.743 |
| Nederlandse tijd | **23:43:30 CET** |
| Register | **H3018 — WorkMode** |
| Geschreven waarde | **2 — Multi-Parallel** |
| Modbus-functie | FC06 — Write Single Register |
| Bron | `DEV_TCP` |
| Client | `[redacted DEV_TCP client IP]:37924` |

Dat is een doorslaggevende bevinding.

De wijziging vond plaats ongeveer 42 minuten na de tweede Winter Mode-activatie door Shine, maar **de H3018-write kwam niet van Shine**. Hij kwam van een TCP-client op `[redacted DEV_TCP client IP]`.

Dat verklaart ook waarom we de wijziging in de historische scans zagen tussen 23:10 en 23:53.

## De inverter heeft de write ook bevestigd

Het log bevat zowel het verzoek als het antwoord van de inverter:

```text
Request:  01 06 0B CA 00 02 2A 11
Response: 01 06 0B CA 00 02 2A 11
```

De broker rapporteert bovendien:

```text
event:            tcp_write_acknowledged
physical_success: true
retry_count:      0
start:            3018
values:           [2]
duration_ms:      20.239
```

Dit sluit een louter interne wijziging van H3018 op dat moment uit. Een externe Modbus-client heeft de wijziging aangevraagd en de inverter heeft deze bevestigd.

### Wat betekent dit voor het VPP-onderzoek?

De belangrijkste nieuwe vraag is nu: **welke applicatie draaide op [redacted DEV_TCP client IP] en waarom schreef die H3018=2?**

Dat kan bijvoorbeeld een Home Assistant-integratie, een ontwikkelscript of een andere Modbus-client zijn geweest. Het IP-adres identificeert de machine of netwerkinterface, maar nog niet het verantwoordelijke proces.

De volledige capture bevat geen writes naar H7261 of H7271. Hun wijzigingen kunnen dus intern of via een niet-geregistreerd mechanisme zijn ontstaan.

Het is nu niet meer gerechtvaardigd om te stellen dat VPP zelf H3018 naar Multi-Parallel heeft gezet. Het is nog wel mogelijk dat de VPP-experimenten indirect aanleiding waren voor de externe write.

**Mijn advies:** voordat we issue #494 bijwerken, achterhalen welke client `[redacted DEV_TCP client IP]` was op 14 november. Daar ligt nu de sleutel tot de oorzaak. Het lijkt er in ieder geval op dat we de daadwerkelijke H3018-write hebben gevonden, inclusief exact tijdstip, herkomst en succesvolle bevestiging.

* * *

**You:**

Dat is dan duidelijk, de `DEV_TCP` poort wordt gebruikt door de HA dev container, waar ook codex in draait. Ik heb codex de TOU tabel laten herschrijven en ik vermoed dat hij daarbij ook ten onrechte dit register heeft geschreven.

* * *

**ChatGPT:**

Dat lijkt me inderdaad de meest waarschijnlijke verklaring. Daarmee valt de verdenking dat Winter Mode/VPP zelf de WorkMode heeft veranderd grotendeels weg.

De tijdlijn past ook:

- **14 november, 23:01 CET:** Shine activeert Winter Mode via VPP.
- **14 november, 23:43:30 CET:** de HA-devcontainer schrijft via `DEV_TCP` de waarde `H3018=2` (Multi-Parallel).
- **15 november:** de TOU-tabel wordt handmatig opgeschoond en opnieuw geschreven.
- **22 november:** na herstel van `H3018=0` laadt de batterij weer normaal.

Wel zit er een interessante nuance in: volgens onze eerdere reconstructie vonden de gedocumenteerde TOU-herstelwrites pas op **15 november tussen 02:29 en 02:32 CET** plaats. De H3018-write gebeurde dus bijna drie uur eerder.

Dat kan betekenen dat Codex al eerder een poging deed om de TOU-configuratie te herstellen, maar dat moeten we nog verifiëren.

### Hoe kan Codex per ongeluk H3018 hebben geschreven?

Een paar mogelijkheden zijn:

- Een foutief startadres bij een Modbus-write.
- Verwarring tussen absolute holding-registeradressen en offsets.
- Een script dat naast de TOU-tabel ook de WorkMode configureerde.
- Een reset- of initialisatiesequentie waarin onbedoeld `H3018=2` stond.

Omdat het een afzonderlijke **FC06-write rechtstreeks naar H3018** betrof, lijkt een eenvoudige verschuiving binnen een FC16-blok minder waarschijnlijk. Er is kennelijk expliciet een write naar dat adres uitgevoerd.

Ik zou daarom de Git-geschiedenis en eventuele Codex-rapporten van 14 november controleren op `3018`, `WorkMode`, `Multi-Parallel` en `FC06`.

**Voor issue #494 betekent dit dat we H3018 voorlopig niet als VPP-bug moeten rapporteren.** De overige problemen met Winter Mode, VPP-registers en TOU blijven natuurlijk wel overeind.

En misschien is dit nog wel de belangrijkste praktische les: de ontwikkelomgeving heeft via `DEV_TCP` de mogelijkheid om ook buiten de bedoelde TOU-registers wijzigingen aan te brengen. Voor toekomstige experimenten lijkt een **write-allowlist op de broker** me verstandig, zodat Codex uitsluitend vooraf toegestane registers kan wijzigen.

* * *

**You:**

Dat lijkt inderdaad verstandig, dit soort installatie instellingen kunnen gevaarlijk zijn. Deze viel mee qua impact, maar bijv. grid parameters of regulation parameters kunnen volgens mij daadwerkelijk schade aanbrengen.

* * *

**ChatGPT:**

Absoluut. En dit is een goede aanleiding om de architectuur van de `growatt-rtu-broker` aan te scherpen. Vooral omdat Codex vanuit de HA-devcontainer rechtstreeks Modbus-writes kan uitvoeren op een fysieke inverter die verbonden is met het elektriciteitsnet.

**Een programmeerfout mag nooit zomaar kunnen leiden tot het wijzigen van veiligheidskritische inverterparameters.**

Ik zou hiervoor een onderscheid maken tussen drie toegangsniveaus:

| Niveau | Toegang | Voorbeelden |
| --- | --- | --- |
| Read-only | Alle toegestane leesregisters, geen writes | Registerscans, firmwareonderzoek, monitoring |
| Operational | Alleen expliciet toegestane writes | TOU, laad-/ontlaadlimieten, SOC-instellingen |
| Administrative | Gevoelige configuratie, standaard geblokkeerd | WorkMode, netbeveiliging, landcode, grid parameters, firmware-updates |

### Mijn voorstel: beveiliging in de broker zelf

**1. Default-deny voor alle writes vanuit `DEV_TCP`**

Alleen een expliciete allowlist van bekende operationele registers. `H3018` wordt daardoor automatisch geweigerd.

---

**2. Bescherm ook de officiële Shine-verbinding tegen onbedoelde wijzigingen vanuit de ontwikkelomgeving**

Geef Shine een afzonderlijk beleid, zodat normale portalfuncties blijven werken. Beveiliging mag niet uitsluitend op het opgegeven client-IP vertrouwen.

---

**3. Valideer volledige Modbus-transacties**

Controleer bij FC06 het register en de waarde. Bij FC16 moeten *alle* registers in het schrijfblok toegestaan zijn. Anders de volledige transactie weigeren, zonder gedeeltelijke uitvoering.

---

**4. Auditlog voor iedere geweigerde en toegestane write**

Registreer timestamp, client, unit-ID, functicode, registers, waarden en de beslissing van de beveiligingslaag.

### Een extra belangrijk punt

Een allowlist op registers alleen is niet voldoende.

Neem `H30100` (VPP control authority). Dit is een operationeel register, maar activering kan ingrijpende gevolgen hebben voor de manier waarop de inverter wordt aangestuurd. Ook het uitschakelen van exportbegrenzing kan ongewenst zijn.

Ik zou daarom daarnaast **waardebereiken, toegestane combinaties en eventueel de actuele inverterstatus** controleren.

En voor de echt kritische instellingen — netspanning, frequentiebeveiliging, anti-islanding, landcodes — zou ik vanuit de ontwikkelomgeving helemaal geen schrijfrechten toestaan. Zulke parameters kunnen veiligheidsfuncties en de netconformiteit aantasten, ook als ze niet onmiddellijk fysieke schade veroorzaken.

Een administratieve uitzondering zou uitsluitend via een afzonderlijk, bewust geactiveerd onderhoudskanaal beschikbaar moeten zijn, niet via dezelfde poort die Codex gebruikt.

### Hoe dit in onze bestaande workflow past

Het mooie is dat dit goed aansluit bij Alchemy Growatt: Codex kan uitgebreid experimenteren met de offline firmware, registers analyseren en tests ontwikkelen, zonder dat iedere testcode dezelfde bevoegdheden krijgt als een installateur.

Ik zou de broker zelfs laten starten met een **read-only ontwikkelpoort**, en een aparte, streng beperkte operationele write-poort. De bestaande `DEV_TCP` kan dan niet per ongeluk door een ruim geformuleerde Codex-opdracht worden gebruikt om installatieparameters te wijzigen.

Als volgende stap lijkt het mij verstandig om eerst de bestaande brokerimplementatie en de Modbus-writepaden te laten auditen. Daarbij moeten we ook kijken naar alternatieve schrijffuncties, protocolvarianten en eventuele routes waarmee de allowlist kan worden omzeild.

**Belangrijkste ontwerpprincipe: de broker handhaaft de grenzen, niet de instructies aan Codex.** Zelfs wanneer een agent een onjuist script genereert, moet de broker de gevaarlijke write zelfstandig blokkeren.

* * *

**You:**

Een read-only ontwikkelpoort is waardeloos als je scripts en automatiseringen wilt ontwikkelen die juist moeten schrijven.

* * *

**ChatGPT:**

Klopt. Een read-only ontwikkelpoort ondermijnt juist het doel van de broker: veilig kunnen experimenteren met echte Modbus-writes op de inverter.

Het probleem is niet dat Codex kan schrijven, maar dat Codex **zonder beperkingen naar registers kan schrijven die buiten het experiment vallen**.

Ik zou daarom kiezen voor een **write-enabled ontwikkelpoort met een configureerbare allowlist**.

### Voorstel

- **Alle reads toegestaan**, zodat volledige registerscans en firmwareonderzoek mogelijk blijven.
- **Writes standaard geweigerd**, behalve naar expliciet vrijgegeven registers.
- **Per register configureerbare waardebereiken**, bijvoorbeeld voor SOC en laadvermogen.
- **FC06 en FC16 ondersteunen**, waarbij een FC16-transactie volledig wordt gecontroleerd.
- **Tijdelijke uitbreiding van de allowlist**, zodat nieuwe registers onderzocht kunnen worden zonder de broker aan te passen.
- **Auditlogging**, inclusief geweigerde writes en de client die ze probeerde uit te voeren.

Daarbij zou ik onderscheid maken tussen gewone operationele registers en installatie- of veiligheidsparameters, zonder die laatste automatisch vrij te geven.

### Belangrijk: geen hardcoded lijst

Ik zou de toegangscontrole in een YAML-bestand configureren, bijvoorbeeld:

```yaml
dev_write_policy:
  default: deny

  allow:
    - register: 3038
      count: 22
      description: "Legacy TOU table"

    - register: 3067
      min: 15
      max: 95
      description: "On-grid discharge stop SOC"

    - register: 30100
      values: [0, 1]
      description: "VPP control authority"

  deny:
    - register: 3018
      description: "Inverter working mode"
```

Dit is een *illustratief* beleid, geen gevalideerde configuratie voor direct gebruik. Met name `H30100` zou ik niet zonder afzonderlijke beoordeling vrijgeven.

Bij een nieuw experiment kan de allowlist bewust worden uitgebreid. Een script dat bijvoorbeeld per ongeluk `H3018=2` probeert te schrijven, wordt dan geweigerd, terwijl de bedoelde TOU-writes gewoon doorgaan.

**Ik zou bovendien de allowlist buiten de devcontainer beheren.** Anders kan Codex, dat zelf code en configuratie aanpast, ook zijn eigen toegangsbeperkingen opheffen.

De broker op de Raspberry Pi wordt daarmee de veiligheidsgrens tussen de ontwikkelomgeving en de fysieke installatie. Je behoudt alle mogelijkheden voor live ontwikkeling, maar een onbedoelde write naar een installatieparameter kan niet meer ongemerkt worden uitgevoerd.

* * *

**You:**

Ik ben er niet zo bang voor de allowlist binnen de devcontainer te beheren, te meer ook omdat de broker remote draait op de echte RPi. Ik ga er niet van uit dat Codex bewust probeert iets te saboteren maar dat hij eenvoudigweg een vergissing gemaakt heeft.  Maar goed, er wordt goede voortgang geboekt op een alternatieve firmware voor de shine (zie laatste commits growatt-firmware repo) en we overwegen dat als broker 2.0 te gaan gebruiken. Ik vraag me nog wel af hoe het zit met o.a. het serienummer van de datalogger. Dat zou dan wel bewaard moeten worden of ingesteld moeten kunnen worden om de growatt portal happy te houden en ik denk dat dan ook de firmware identificatie gefaked zou moeten worden.

* * *

**ChatGPT:**

Ik denk dat je met Broker 2.0 op de ShineWiLan-X2 een bijzonder interessante richting inslaat. En je vermoeden over de identiteit van de datalogger blijkt terecht: **het serienummer is daadwerkelijk onderdeel van de cloudcommunicatie en wordt persistent opgeslagen.**

Ik heb de nieuwste ontwikkelbranch bekeken, [github.com](https://github.com/l4m4re/growatt-firmware/tree/codex/shine-x2-open-replacement?utm_source=chatgpt.com), inclusief de commits van vandaag, 22 november.

## 1. Het serienummer is al teruggevonden in de firmwareanalyse

De analyse van de originele ShineWiLan-X2-firmware 7.6.2.5 bevat concrete aanwijzingen:

| Onderdeel | Bevinding |
| --- | --- |
| NVS-sleutel | `datalog_sn` |
| Cloudconfiguratie | `datalog_ip`, `datalog_port` |
| Configuratieopslag | NVS en een afzonderlijke `user_data`-partitie |
| MQTT-identiteit | Wordt opgebouwd uit de runtimeconfiguratie |
| MQTT-topics | `s/33/<identity>` en `c/33/<identity>` |
| Cloudverbinding | `mqtt.growatt.com` |

Dit is geen aanname meer. De statische analyse heeft de gegevensstroom van `datalog_sn` via de configuratietabel naar de MQTT-initialisatie gereconstrueerd.

De precieze authenticatie en alle cloudberichtformaten zijn nog niet volledig gereconstrueerd.

Bron: [WILAN_X2_7.6.2.5_STATIC_EVIDENCE.md](https://github.com/l4m4re/growatt-firmware/blob/codex/shine-x2-open-replacement/shine-analysis/docs/WILAN_X2_7.6.2.5_STATIC_EVIDENCE.md?utm_source=chatgpt.com).

### Wat betekent dat praktisch?

Het lijkt goed mogelijk om de bestaande loggeridentiteit te behouden wanneer je de originele firmware vervangt.

Ik zou de identiteit niet hardcoded in de nieuwe firmware opnemen, maar deze uit de bestaande NVS-configuratie lezen of via een eigen configuratievoorziening kunnen instellen.

Daarmee kun je dezelfde logische datalogger blijven gebruiken, in plaats van bij Growatt een nieuw apparaat te moeten registreren.

Dat de Growatt-cloud een vervangende firmware vervolgens accepteert, is nog niet bewezen.

## 2. Moet je ook de firmwareversie faken?

**Mogelijk wel, maar niet noodzakelijk voor de MQTT-authenticatie.**

Uit de firmwareanalyse blijkt namelijk dat de originele X2 bij MQTT CONNECT de volgende gegevens gebruikt:

| MQTT-veld | Originele ShineWiLan-X2 |
| --- | --- |
| Client ID | Dataloggeridentiteit uit NVS |
| Username | Dezelfde identiteit |
| Password | In de firmware opgenomen [redacted stock-firmware value] |
| Keepalive | 420 seconden |
| Topicidentiteit | Afgeleid van hetzelfde serienummer |

Opvallend is dus dat de firmwareversie geen onderdeel lijkt te zijn van de MQTT CONNECT-authenticatie.

De firmware bevat daarnaast wel versie- en identificatiestrings, waaronder `7.6.0.6`, `V1.0` en `GTSW0000`. De analyse heeft nog niet vastgesteld hoe deze velden in de daadwerkelijk gebruikte cloudberichten terechtkomen.

Ik zou daarom twee zaken onderscheiden:

**Dataloggeridentiteit behouden:** essentieel om dezelfde logger in de bestaande Growatt-installatie te laten functioneren.

**Firmware-identificatie emuleren:** waarschijnlijk nuttig voor compatibiliteit met de portal, maar pas noodzakelijk als we aantonen dat de cloud die velden controleert.

Er zit nog een risico aan: als de cloud denkt dat de originele firmware draait, kan hij automatische firmware-updates aanbieden. Broker 2.0 moet voorkomen dat zo'n update zonder expliciete toestemming de alternatieve firmware overschrijft.

## 3. De originele configuratie behouden

De flashanalyse toont deze indeling:

ShineWiLan-X2 — 16 MiB flash

0x001000 · Bootloader

0x008000 · Partition table

0x009000 · NVS — datalog_sn, netwerkconfiguratie

0x00D000 · OTA metadata

0x010000 · ota_0

0x280000 · ota_1 — originele 7.6.2.5

0x600000 · user_data — configuratierecords

Vereenvoudigde weergave van de relevante partities; niet op schaal.

Daaruit volgt een nuttige migratiestrategie: maak vóór het flashen een volledige backup en behoud de originele NVS- en `user_data`-partities. De bestaande firmware bevat bovendien al code die configuratie tussen deze opslaggebieden synchroniseert.

Ik zou de nieuwe firmware in eerste instantie in een afzonderlijke OTA-slot ontwikkelen en de oorspronkelijke installatie herstelbaar houden. Daarbij moet wel worden gecontroleerd of de nieuwe firmware de bestaande NVS-indeling correct begrijpt.

## 4. Wat betekent dit voor Broker 2.0?

Ik zou de architectuur splitsen in een **lokale control plane** en een **Growatt-compatibiliteitslaag**.

**Home Assistant / Codex / scripts**

Modbus TCP, registerscans, experimenten

↓

Broker 2.0 — ShineWiLan-X2

Lokale broker

Write-filter, Modbus, auditlog

Cloudclient

SN, MQTT, portalprotocol

---

Eén gedeelde, geserialiseerde Modbus-transactielaag

↓

**Growatt MIN 6000TL-XH**

Fysieke UART/Modbus-verbinding

De huidige firmwareanalyse heeft al aangetoond dat de originele Shine een gedeelde transactielaag gebruikt voor lokale Modbus TCP- en bepaalde cloudcommando's. Dat is precies het ontwerpprincipe dat je bij Broker 2.0 wilt behouden: één eigenaar van de fysieke inverterverbinding.

## 5. Wat ik Codex nu zou laten uitzoeken

De recente commits, waaronder [Parse raw PiITM transaction capture streams](https://github.com/l4m4re/growatt-firmware/commit/8fae4ce31e6ae3942114c719492ea3f6daf227fb?utm_source=chatgpt.com), concentreren zich op reconstructie van de cloud- en transportcommunicatie.

Daar zou ik één gerichte onderzoeksvraag aan toevoegen:

*Reconstruct the ShineWiLan-X2 datalogger identity and firmware-version reporting paths, determine which fields are required for Growatt cloud compatibility, and design preservation of the existing NVS/user_data configuration across an alternative firmware installation.*

Daarbij vooral controleren of de firmwareversie alleen voor inventarisatie en OTA-updates wordt gebruikt, of ook een rol speelt bij autorisatie van cloudcommando's.

**Mijn voorlopige inschatting:** het bestaande serienummer behouden lijkt relatief eenvoudig en is inmiddels goed onderbouwd. Het faken van de firmwareversie is wellicht helemaal niet nodig om de portal te laten functioneren.

De echte resterende uitdaging is de volledige cloudprotocolcompatibiliteit, inclusief statusupdates, commando's, acknowledgements en OTA-afhandeling. Daar wordt in de huidige branch aantoonbaar voortgang op geboekt.

* * *

**You:**

Volgens mij gaan firmware updates voor de inverter en betterij ook over modbus en als het goed gedaan wordt zou er geen risico moeten zijn v.w.b. flashen, maar dat weet je nooit zonder testen en dat wil je niet met een live inverter. Maar goed, volgens mij zit er in principe al iets van web configuratie in, dus die zouden we kunnen aanpassen om o.a. serienummer te configureren als kloon van een andere shine. Ik denk er over een tweede shine aan te schaffen om als broker te configureren, terwijl de andere origineel blijft.

* * *

**ChatGPT:**

Dat lijkt me eigenlijk een veel betere aanpak dan de bestaande ShineWiLan-X2 meteen van alternatieve firmware te voorzien. Zeker omdat je de Growatt-portal wilt behouden en ondertussen steeds verder komt met het reconstrueren van de communicatie.

**Met twee ShineWiLan-X2's kun je de originele logger als referentie houden en de tweede gebruiken als ontwikkelplatform voor Broker 2.0.** Daarmee kun je de alternatieve firmware daadwerkelijk op hardware testen, zonder meteen je werkende installatie op het spel te zetten.

Er zit wel een belangrijk onderscheid tussen het klonen van de *identiteit* en het gelijktijdig gebruiken van twee apparaten met dezelfde identiteit.

## 1. Twee Shine-loggers, één serienummer

Ik zou de vervangende firmware inderdaad voorzien van een configureerbaar dataloggerserienummer, maar met een duidelijke scheiding tussen de fysieke identiteit van het apparaat en de identiteit waarmee het zich bij Growatt aanmeldt.

| Identiteit | Originele Shine | Broker 2.0 |
| --- | --- | --- |
| Fysiek serienummer | Origineel | Eigen serienummer |
| Growatt cloud-ID | Origineel | Configureerbaar |
| MAC-adres | Origineel | Eigen MAC-adres |
| Firmwareversie | Growatt 7.6.2.5 | Eigen versie, eventueel compatibiliteitsidentificatie |

Het voordeel is dat Broker 2.0 zich bij de portal kan presenteren als de bestaande datalogger, zonder de oorspronkelijke hardware-identiteit uit te wissen.

**Beide loggers tegelijkertijd met hetzelfde serienummer op de Growatt-cloud aanmelden zou ik vermijden.** Dat kan conflicten veroorzaken bij MQTT-sessies, commandoafhandeling of de toewijzing van meetgegevens.

De originele Shine kan dan gewoon offline blijven tijdens de cloudtests met Broker 2.0. Wanneer iets niet werkt, wissel je terug.

## 2. Firmware-updates van de inverter en ARK

Ik denk dat je gelijk hebt dat een belangrijk deel van het updateproces over de seriële verbinding met de inverter loopt.

Onze ZABA22-analyse heeft al verschillende programmeer- en flashpaden blootgelegd. Dat ondersteunt het idee dat de datalogger als transport voor firmware-updates kan fungeren.

Maar er zijn twee verschillende risico's:

- **Firmware-update van de Shine zelf:** risico voor de ESP32 en zijn flashpartities. Dit kun je op de tweede logger onderzoeken.
- **Firmware-update van inverter of ARK:** risico voor de besturing van de installatie. Zelfs als de transportlaag perfect werkt, kan een onderbroken of onvolledige programmeersequentie problemen veroorzaken.

Voor die tweede categorie zou ik voorlopig alleen passieve analyse en offline replay gebruiken. Een echte update van de live inverter is niet nodig om Broker 2.0 voor normaal Modbus-verkeer te ontwikkelen.

## 3. Er bestaat inderdaad al een configuratie-interface

Ik heb ook de officiële [ShineWiLan-X2 Configuration Guide](https://growatt.tech/wp-content/uploads/2025/01/ShineWiLan-X2_Configuration_Guide_EN_202412.pdf?utm_source=chatgpt.com) bekeken. De X2 heeft via Bluetooth en ShinePhone/ShineTools een configuratie-interface met onder andere serienummer, firmwareversie, serverinstellingen en netwerkconfiguratie. Het is dus niet noodzakelijk een klassieke webinterface, maar de functies die je zoekt bestaan al grotendeels.

[igrowattinverter.com](https://igrowattinverter.com/wp-content/uploads/2025/02/ShineWiLan-X2_Configuration_Guide_EN_202412.pdf?utm_source=chatgpt.com) · [growatt.tech](https://growatt.tech/wp-content/uploads/2025/01/ShineWiLan-X2_Configuration_Guide_EN_202412.pdf?utm_source=chatgpt.com)

Dat betekent dat we voor Broker 2.0 niet helemaal opnieuw hoeven te bedenken hoe configuratie zou moeten werken. Een eenvoudige lokale webinterface zou zelfs prettiger zijn, zolang die afzonderlijk van de cloudcommunicatie functioneert.

Interessant is dat Growatt bij een factory reset expliciet het serienummer en de serverinstellingen bewaart. Die gegevens zijn dus bewust gescheiden van de gewone gebruikersinstellingen.

[igrowattinverter.com](https://igrowattinverter.com/wp-content/uploads/2025/02/ShineWiLan-X2_Configuration_Guide_EN_202412.pdf?utm_source=chatgpt.com)

## 4. Een tweede X2 is bovendien niet duur

![Growatt ShineWiLan-X2](https://images.openai.com/thumbnails/url/K3aKn3icDcnbDoIgAADQLzLLsNvWWpm6YXcUojcjUkTRlHR-Vp_T39R5Pd9PqnXVLEyTK1b3leYPQ9_VeJA0OtaCDVhZmE1aVpVQyeq1_N9ifXjMfYYSyI3W6ywsYxjJoKQ59i9q6kCCmQ9oVoKCECgdtLHRO6_Xqt7Z6jhxuXsC-z4KoOi35Dm8dmFIzwJ4iJ1577X4yVEUS34EWm6swBWdHtU4z7qbnA2pS5Mf540_Wg) · **

Growatt ShineWiLan-X2

**

€ 19,99

Bij solago NL + andere, exclusief eventuele verzendkosten

Ook

Growatt ShineWiLan-X2 Datalogger

 wordt aangeboden voor € 21,45 bij NKON + andere.

Voor ongeveer twee tientjes is een tweede exemplaar een betrekkelijk goedkope manier om het project van een offline emulator naar echte hardwareontwikkeling te brengen.

## 5. Hoe ik de twee loggers zou inzetten

Ik zou beginnen met de tweede Shine volledig los van de live inverter te programmeren en te testen. Vervolgens kun je met een geïsoleerde Modbus-simulator de communicatie en eventueel firmware-updateverkeer testen. Pas wanneer dat voldoende betrouwbaar is, wissel je de originele Shine tijdelijk om voor Broker 2.0.

Er is nog een interessant alternatief: **Broker 2.0 hoeft niet noodzakelijk alle firmware-updatecommando's inhoudelijk te begrijpen.**

Voor de meeste normale Modbus-transacties kan hij immers als transparante transportlaag functioneren. Voor firmware-updates is dat ingewikkelder: als er speciale commando's, sessiestatus, time-outs of bootloadermodi bij betrokken zijn, moet hij die correct ondersteunen. Tot die tijd kunnen zulke operaties beter expliciet geblokkeerd worden.

Bij het ontwerpen zou ik daarom een onderscheid maken tussen:

- De eigen OTA-update van de ESP32.
- Doorsturen van firmware-updates naar de inverter en ARK.
- Gewone Modbus-transacties voor monitoring en besturing.

Mijn voorkeur zou zijn om de **originele firmware-updatefunctionaliteit voorlopig niet na te bouwen**, maar eerst Broker 2.0 werkend te krijgen als vervanging voor de Raspberry Pi-broker, inclusief Growatt-cloudcompatibiliteit.

En misschien het aantrekkelijkste: als het eenmaal goed werkt, heb je geen Raspberry Pi meer nodig om lokaal Modbus TCP beschikbaar te maken. De ShineWiLan-X2 wordt dan zelfstandig de broker en Growatt-datalogger in één apparaat.

De oorspronkelijke Shine blijft intact als directe terugvalmogelijkheid.

* * *

**You:**

Ik denk dat we firmware updates niet moeten willen. Als er iets mis gaat zit je met de gebakken peren, terwijl je vrij eenvoudig handmatig firmware updates kunt uitvoeren door even het origineel in te pluggen. Ik zou de alternatieve firmware graag als open source willen publiceren, maar wellicht is dat niet mogelijk zonder copyrighted delen van de originele firmware te reverse engineeren. De compaq case heeft ooit laten zien dat een clean-room her implementatie toegestaan is.  Hoe staan we op dat vlak?

* * *

**ChatGPT:**

**Ik denk dat jullie behoorlijk goede mogelijkheden hebben om Broker 2.0 als open source te publiceren.** Het Europese auteursrecht biedt daarvoor zelfs expliciet ruimte. Maar er is een belangrijk verschil tussen het legaal reverse-engineeren van firmware en het mogen publiceren van alle onderzoeksresultaten en afgeleide code.

Ik heb de Europese regelgeving, de Nederlandse Auteurswet en de huidige `growatt-firmware`-branch bekeken. Mijn voorlopige beoordeling is dat de implementatie al grotendeels de goede kant op gaat, maar dat ik vóór publicatie een aantal onderdelen zou scheiden.

## 1. De juridische basis is gunstig

Je verwijzing naar de IBM-compatible BIOS-implementatie van Compaq is terecht. De clean-room-benadering uit die geschiedenis is nog steeds relevant.

Voor Nederland hebben we echter een sterkere, specifiekere wettelijke basis.

### Europese Richtlijn 2009/24/EG

Deze richtlijn beschermt computerprogramma's, maar maakt tegelijk onderscheid tussen beschermde code en de ideeën, principes en interfaces daarachter.

| Bepaling | Betekenis voor Broker 2.0 |
| --- | --- |
| Artikel 1(2) | Ideeën en beginselen achter een programma zijn niet auteursrechtelijk beschermd |
| Artikel 5(3) | Een rechtmatige gebruiker mag de werking observeren, bestuderen en testen |
| Artikel 6 | Decompilatie is onder voorwaarden toegestaan wanneer noodzakelijk voor interoperabiliteit |
| Artikel 6(2) | Verkregen informatie mag niet worden gebruikt voor ongeoorloofd kopiëren of andere doeleinden buiten de interoperabiliteitsuitzondering |

De richtlijn is in Nederland onder meer verwerkt in **artikel 45l en 45m van de Auteurswet**.

[eur-lex.europa.eu](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX%3A32009L0024&utm_source=chatgpt.com) · [OpenWet](https://openwet.nl/nl/BWBR0001886/2026-01-01.html?utm_source=chatgpt.com)

Het Hof van Justitie heeft bovendien in de SAS Institute/WPL-zaak bevestigd dat de functionaliteit van een computerprogramma, de programmeertaal en het formaat van gegevensbestanden als zodanig niet door het softwareauteursrecht worden beschermd. Dat is relevant voor het nabouwen van een communicatieprotocol.

[curia.europa.eu](https://curia.europa.eu/jcms/jcms/P_87138/de/?utm_source=chatgpt.com) · [EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A62020CJ0013&utm_source=chatgpt.com)

Er gelden wel voorwaarden. Zo mag decompilatie voor interoperabiliteit niet verder gaan dan noodzakelijk, moet de benodigde informatie niet al gemakkelijk beschikbaar zijn en mag de verkregen informatie niet worden gebruikt om beschermde expressie over te nemen.

**Een zelfstandig geschreven implementatie van het Growatt-protocol is dus in beginsel mogelijk zonder toestemming van Growatt.** Dat betekent niet automatisch dat iedere vorm van firmwareanalyse of hergebruik is toegestaan.

## 2. Hoe staat de huidige repository ervoor?

Ik heb specifiek gekeken naar [provenance-and-license-review.md](https://github.com/l4m4re/growatt-firmware/blob/codex/shine-x2-open-replacement/open-firmware/docs/provenance-and-license-review.md?utm_source=chatgpt.com).

Daarin staat al expliciet dat de nieuwe implementatie zelfstandig geschreven wordt op basis van waargenomen interfaces en onderzoeksbewijs, en dat originele firmwarebytes en decompileroutput niet in de implementatiebroncode worden opgenomen.

Dat is precies het onderscheid dat we willen.

**Huidige beoordeling van de publicatiegereedheid** · (Nog niet vrijgeven)

| Eigen C/Python-implementatie | Gunstig uitgangspunt |
| --- | --- |
| Protocoldefinities en Modbus-frames | In beginsel geschikt |
| Eigen tests en simulatoren | In beginsel geschikt |
| Originele Growatt-firmwareimages | Niet meepubliceren |
| Ghidra-decompilaties en broncodeachtige reconstructies | Juridisch beoordelen en scheiden |
| TI/ST-code en andere afhankelijkheden | Licenties controleren |
| Definitieve open-sourcelicentie | Nog niet gekozen |

De repository documenteert deze beperkingen zelf al. Dat is positief, maar nog geen volledige juridische controle van de feitelijke inhoud van ieder bestand.

## 3. Het grootste risico zit niet in de nieuwe firmware, maar in de onderzoeksrepository

De huidige `growatt-firmware`-repository bevat naast de open implementatie ook firmwareanalyse, gedecompileerde pseudocode, reconstructies van interne algoritmen en originele firmwarebestanden.

Dat is voor onderzoek begrijpelijk, maar voor een publieke release minder aantrekkelijk.

Er zijn drie categorieën die ik strikt zou onderscheiden:

- **Protocolfeiten:** registeradressen, functienummers, pakketformaten, checksums, timings en waargenomen responses. Deze kunnen doorgaans als interoperabiliteitsinformatie worden gebruikt.
- **Oorspronkelijke creatieve expressie:** firmwarebytes, originele code, omvangrijke decompilaties en mogelijk delen van Growatts documentatie. Deze zou ik niet in de release opnemen.
- **Zelfstandige implementatie:** eigen code die dezelfde interface implementeert, met eigen algoritmen en datastructuren. Dit is de beoogde open-sourcecode.

Bijzonder relevant is dat Codex inmiddels behoorlijk diep in ZABA22 en de originele Shine-firmware is doorgedrongen. Een nieuwe implementatie die sterk de interne code- en datastructuur van Growatt volgt, kan juridisch lastiger te verdedigen zijn dan een implementatie die alleen hetzelfde externe protocolgedrag reproduceert.

Een formele scheiding tussen twee teams is overigens **geen wettelijke voorwaarde** voor een clean-room-implementatie. Ook één ontwikkelaar kan zelfstandig nieuwe code schrijven op basis van rechtmatig verkregen interface-informatie. Een gescheiden ontwikkelproces maakt de onafhankelijkheid vooral gemakkelijker aantoonbaar.

### Mijn voorstel: twee afzonderlijke repositories

**growatt-firmware-research**

Onderzoeksarchief met originele images, Ghidra-projecten, decompilaties, interne geheugenstructuren en gedetailleerde firmwareanalyse. Toegang en publicatierechten afzonderlijk beoordelen.

---

**open-growatt-shine**

Publieke, zelfstandig geschreven ESP32-firmware met Modbus-broker, configureerbare loggeridentiteit, cloudcompatibiliteit, eigen tests en alleen publiceerbare protocolspecificaties.

De onderzoeksrepository is nu al op GitHub beschikbaar. Het scheiden van de publiceerbare onderdelen voorkomt toekomstige vermenging, maar verwijdert geen eventueel eerder gepubliceerd materiaal uit de Git-geschiedenis. Dat vraagt dus een afzonderlijke inventarisatie.

## 4. Firmware-updates bewust niet ondersteunen

Je voorstel om de originele Shine tijdelijk terug te plaatsen voor updates vind ik verstandig.

Daarmee verdwijnt een complex en risicovol onderdeel uit de scope van Broker 2.0.

Ik zou zelfs expliciet vastleggen dat de alternatieve firmware:

- geen firmware-updates naar inverter of batterij initieert;
- bekende firmware-updatecommando's niet doorstuurt;
- niet pretendeert dat een firmware-update is uitgevoerd;
- zelf alleen via een gecontroleerde lokale procedure kan worden bijgewerkt.

De eerste twee vereisen dat we alle relevante updatepaden herkennen, inclusief propriëtaire Modbus-functies. Onbekende programmeercommando's mogen dus niet blind doorgestuurd worden.

Dit verkleint zowel het technische risico als de hoeveelheid originele firmwarefunctionaliteit die we moeten reconstrueren. Het maakt het juridische argument voor interoperabiliteit eveneens overzichtelijker, zonder dat het automatisch alle reverse-engineeringhandelingen rechtvaardigt.

## 5. Hoe dicht zijn we bij publicatie?

Technisch is er al een serieuze basis.

De nieuwste branch bevat een ESP-IDF-target, een eigen Modbus TCP-server, een transactiescheduler, een UART-backend en een groeiende MQTT-implementatie. De documentatie meldt ook dat er al een echte ESP32-applicatieimage gebouwd kan worden.

Dat betekent niet dat hij al op de fysieke Shine is gevalideerd. Cloudcompatibiliteit, hardwaregedrag en een aantal beveiligingsgrenzen zijn nog in ontwikkeling.

Juridisch zie ik vooral drie open punten: de herkomst van alle broncode, de licenties van eventuele TI/ST-componenten en de grens tussen gepubliceerde protocolfeiten en mogelijk beschermde gedecompileerde expressie.

Ik zou vóór een eerste publieke release een provenance-audit uitvoeren van de complete source tree, inclusief gegenereerde bestanden en commitgeschiedenis. Daarna kan een passende open-sourcelicentie worden gekozen, bijvoorbeeld Apache-2.0 of GPLv3, afhankelijk van de gebruikte componenten en de gewenste voorwaarden.

Een korte beoordeling door een Nederlandse IE-jurist is verstandig vóór het daadwerkelijk verspreiden van firmwareimages, vooral omdat het project op bestaande commerciële hardware draait.

## 6. Conclusie

**Er is volgens mij geen fundamentele juridische reden waarom een zelfstandig geschreven, Growatt-compatibele Shine-firmware niet open source zou mogen zijn.**

Het Europese recht geeft aanzienlijk meer houvast dan alleen de historische Compaq-casus, vooral door de interoperabiliteitsuitzondering en de uitspraak in *SAS Institute v. WPL*.

[eur-lex.europa.eu](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX%3A32009L0024&utm_source=chatgpt.com) · [EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A62010CJ0406&utm_source=chatgpt.com)

Ik zou daarom niet proberen de originele Growatt-firmware zo volledig mogelijk na te bouwen. Ik zou juist een nieuwe firmware maken die een beperkt aantal duidelijk omschreven interfaces implementeert:

**Modbus RTU ↔ Modbus TCP ↔ Growatt-cloud**, aangevuld met een eigen configuratie-interface.

Dat is precies wat je voor Broker 2.0 nodig hebt.

Het onderzoekswerk aan de originele firmware blijft waardevol als referentie, maar hoeft niet in de publieke implementatie terecht te komen. Daarmee wordt het project juridisch beter verdedigbaar, technisch overzichtelijker en toegankelijker voor andere ontwikkelaars.

En de keuze om firmware-updates aan de originele Shine over te laten maakt die afbakening nog scherper.

* * *

**You:**

Yep, voor firmware updates wil je originele shine gebruiken, niet vanwege copyrights maar vanwege garantie. Als er iets mis gaat met een originele shine kun je bij growatt aankloppen.  Gaat er iets mis met alternatieve firmware, dan kun je een probleem hebben dat eenvoudig te voorkomen is.

* * *

**ChatGPT:**

Precies. Dat is een veel belangrijker argument dan auteursrecht.

Je wilt voorkomen dat je bij een mislukte firmware-update in een discussie met Growatt terechtkomt over de vraag of het probleem door hun firmware, de inverter of jouw alternatieve datalogger is veroorzaakt.

Met de originele Shine houd je de updateprocedure volledig binnen de door Growatt ondersteunde configuratie. Dat geeft je een veel sterkere positie bij eventuele garantie- of supportclaims, al is het natuurlijk geen absolute garantie dat Growatt aansprakelijkheid accepteert.

Daarmee ontstaat een heel logische taakverdeling:

| Originele Shine | Broker 2.0 |
| --- | --- |
| Firmware-updates inverter | Monitoring |
| Firmware-updates ARK | Modbus TCP |
| Officiële Growatt-diagnostiek | Home Assistant-integratie |
| Ondersteunde serviceprocedures | Eigen automatiseringen |
| Terugvalmogelijkheid | Growatt-cloudcommunicatie |

Ik zou dit zelfs als **bewuste ontwerpkeuze** in de open-sourceprojectdocumentatie opnemen: firmware-updates van downstream apparatuur worden niet ondersteund en moeten met de originele Growatt-datalogger worden uitgevoerd.

Daarbij is het belangrijk dat Broker 2.0 firmware-updatecommando's expliciet weigert in plaats van ze half te ondersteunen.

Het mooie van deze aanpak is dat je de originele Shine niet hoeft te modificeren en Broker 2.0 niet alle mogelijkheden van de originele firmware hoeft te reproduceren.

En met een tweede Shine als ontwikkelplatform kun je bovendien de alternatieve firmware testen zonder de officiële servicevoorziening kwijt te raken.

**De oorspronkelijke Shine wordt daarmee feitelijk je service- en recoverytool, terwijl Broker 2.0 je dagelijkse datalogger en besturingsinterface wordt.** Dat lijkt me een prima architectuur voor zowel eigen gebruik als een toekomstig open-sourceproject.

* * *

**You:**

Wil je eens kijken hoe ver alchemy inmiddels is gevorderd met de nieuwe firmware?

* * *

**ChatGPT:**

## Alchemy Growatt — voortgang per 25 november 2026

(Actieve ontwikkeling) · Laatste gecontroleerde commit: 09:14 CET

**Alchemy heeft sinds 22 november flinke vooruitgang geboekt. De nieuwe ShineWiLan-X2-firmware is inmiddels een samenhangende ESP32-implementatie die succesvol wordt gecompileerd, inclusief Modbus TCP, MQTT, fysieke UART en een gezamenlijke transactiebroker.**

De grote beperking is dat de resultaten nog uit offline tests en builds komen. Er is nog geen aangetoonde succesvolle uitvoering op een echte ShineWiLan-X2, laat staan een volledige verbinding met de Growatt-portal en je inverter.

Ontwikkelstatus · Beoordeling op basis van de commits

ESP32-firmware compileren · Geslaagd

Modbus TCP / RTU-broker · Offline getest

MQTT-transport · Uitgebreid getest

Growatt-cloudprotocol · Gedeeltelijk gereconstrueerd

Echte X2-hardware · Niet gevalideerd

Indicatieve inschatting van de technische gereedheid, geen gemeten voltooiingspercentages.

### Recente GitHub-activiteit

De actieve branch is [codex/shine-x2-open-replacement](https://github.com/l4m4re/growatt-firmware/tree/codex/shine-x2-open-replacement?utm_source=chatgpt.com).

Enkele belangrijke commits van vanochtend, Nederlandse tijd

| Tijd | Commit | Betekenis |
| --- | --- | --- |
| 09:14 | [github.com](https://github.com/l4m4re/growatt-firmware/commit/856290d0dd?utm_source=chatgpt.com) | Verouderde MQTT-schrijfcommando's weigeren |
| 09:01 | [github.com](https://github.com/l4m4re/growatt-firmware/commit/c4672af49e?utm_source=chatgpt.com) | MQTT-berichten vasthouden tot SUBACK |
| 08:35 | [github.com](https://github.com/l4m4re/growatt-firmware/commit/3a423e7a9e?utm_source=chatgpt.com) | TLS pas activeren na tijdsynchronisatie |
| 08:17 | [github.com](https://github.com/l4m4re/growatt-firmware/commit/98970c87dd?utm_source=chatgpt.com) | MQTT-keepalive afstemmen op originele Shine |
| 08:06 | [github.com](https://github.com/l4m4re/growatt-firmware/commit/172e6d48e8?utm_source=chatgpt.com) | Volledige FC03-transactie end-to-end testen |
| 06:25 | [github.com](https://github.com/l4m4re/growatt-firmware/commit/4d9432b220?utm_source=chatgpt.com) | Complete ESP32-build verifiëren |
| 06:23 | [github.com](https://github.com/l4m4re/growatt-firmware/commit/2e958573ab?utm_source=chatgpt.com) | Modbus-writes per client autoriseren |

Vooral de combinatie van de complete build en de end-to-end-tests is belangrijk: Alchemy is duidelijk verschoven van protocolreconstructie naar het testen van een geïntegreerde firmwarearchitectuur.

## Wat er technisch bijgekomen is

### 1. Een complete ESP32-build

De recentste bouwtest schakelt de volgende onderdelen tegelijkertijd in:

- Modbus TCP-server met ondersteuning voor reads en writes.
- Fysieke UART/Modbus RTU-backend.
- MQTT-client en Growatt-rapportagepad.
- HTTPS-export van diagnostische traces.
- Optionele Open Controller-interface.

Applicatieimage

**859 KiB**

Past in de 1 MiB-partitie

Vrije ruimte

**165 KiB**

Ongeveer 16%

De build is uitgevoerd met ESP-IDF 4.4.3. Dat is een belangrijke stap, omdat nu niet alleen individuele modules compileren, maar de onderdelen ook samen in één firmwareimage passen.

De gebruikte UART-pinnen zijn nog **testconfiguratie**; daarmee is de werkelijke ShineWiLan-X2-pinout niet bevestigd.

### 2. De broker is behoorlijk volwassen geworden

Een mooie verbetering is de end-to-end-test met een virtuele inverter. Die stuurt een FC03-read van 125 registers door de volledige keten:

Modbus TCP-client

↓

Broker / transactiescheduler

↓

RTU-adapter

↓

Virtuele inverter

De test vergelijkt het volledige verzoek en antwoord, inclusief CRC, MBAP-header en correlatie-ID.

De volledige RTU-response van 255 bytes en de Modbus TCP-response van 259 bytes worden gecontroleerd. Ook de tussenliggende gebeurtenissen verschijnen in het auditlog.

Dat sluit mooi aan bij wat we eerder met de Raspberry Pi-broker hebben bereikt.

### 3. Beveiliging van Modbus-writes

Na ons incident met `H3018=2` is dit bijzonder relevant.

Alchemy heeft inmiddels een mechanisme toegevoegd waarmee **iedere lokale FC06/FC16-write afzonderlijk per netwerkclient wordt geautoriseerd**. De standaardimplementatie weigert alle writes.

Dat is nog geen volledige register-allowlist, maar wel een nuttige eerste beveiligingslaag. De ontwikkelcontainer kan straks schrijfbevoegdheid krijgen zonder dat automatisch iedere willekeurige TCP-client die bevoegdheid heeft.

### 4. De cloudimplementatie wordt steeds nauwkeuriger

Hier zit misschien de meeste recente onderzoeksinspanning.

Alchemy heeft inmiddels onder andere:

- De MQTT-keepalive en sessieflags afgestemd op het gedrag uit de originele firmware.
- De afhandeling van persistent sessions en SUBACK verbeterd.
- Ondersteuning toegevoegd voor gecomprimeerde Growatt type-8-enveloppen met LZ4.
- De antwoordformaten voor cloudcommando's verder gereconstrueerd.
- Bescherming ingebouwd tegen oude schrijfcommando's die na het herstellen van een MQTT-sessie opnieuw worden aangeboden.

Vooral dat laatste vind ik verstandig. Een verouderd commando om een batterijlimiet te wijzigen mag niet alsnog uitgevoerd worden wanneer de verbinding later terugkomt.

De cloudcode is daarmee inhoudelijk een stuk verder dan op 22 november. Maar de repository benadrukt nog steeds dat de authenticatie, werkelijke berichtuitwisseling en portalcompatibiliteit niet met een echte Growatt-cloudsessie zijn gevalideerd.

## Wat ontbreekt nog?

| Onderdeel | Huidige toestand |
| --- | --- |
| Uitvoering op echte ShineWiLan-X2 | Nog niet aangetoond |
| Definitieve UART-pinout en elektrische interface | Nog niet gevalideerd |
| Verbinding met echte Growatt-cloud | Nog niet aangetoond |
| WiFi-provisioning | Interface aanwezig, daadwerkelijke provisioning ontbreekt |
| Configureerbaar dataloggerserienummer | Opslagstructuur aanwezig; nog geen complete gebruikersinterface |
| Webconfiguratie | Nog niet aangetoond als werkende interface |
| Registergerichte write-allowlist | Nog niet compleet |
| Open-sourcerelease | Licentie- en herkomstcontrole nog open |

Bronnen: [Actuele ontwikkelstatus](https://github.com/l4m4re/growatt-firmware/blob/codex/shine-x2-open-replacement/open-firmware/docs/shine-x2-open-replacement-backlog.md?utm_source=chatgpt.com) en [ESP32-target en testinstructies](https://github.com/l4m4re/growatt-firmware/blob/codex/shine-x2-open-replacement/open-firmware/target/shine_x2/espidf/README.md?utm_source=chatgpt.com).

## Mijn beoordeling

De grootste technische hobbel lijkt inmiddels niet meer de brokerarchitectuur zelf. Er is nu een geïntegreerde firmware, een geserialiseerde transactielaag en een behoorlijk uitgebreid testsysteem.

**De volgende belangrijke mijlpaal is uitvoering op de tweede fysieke ShineWiLan-X2, aanvankelijk zonder verbinding met je live inverter.**

Daarbij zou ik eerst de originele flashinhoud en configuratie veiligstellen, de hardware-pinout bevestigen en vervolgens alleen netwerk- en Modbuscommunicatie met een simulator testen.

Daarna wordt de Growatt-cloud de belangrijkste resterende uitdaging. Alchemy heeft veel van het protocol gereconstrueerd, maar zonder een echte communicatietrace blijft onzeker of de portal alle statusberichten en commando's correct zal verwerken.

Mijn indruk is dat de voortgang technisch aanzienlijk is: **van firmwareonderzoek naar een daadwerkelijk compileerbare vervangende firmware met uitgebreide offline integratietests, in slechts enkele dagen.** De stap naar een betrouwbare, dagelijks bruikbare Broker 2.0 moet echter nog op hardware worden bewezen.

* * *

**You:**

Wil je kijken hoe de vlag er bij hangt en je mening delen over hoe alchemy presteert bij deze goal?

* * *

**ChatGPT:**

## Alchemy Growatt — stand van zaken op 26 november 2026

(Actieve ontwikkeling) · Laatste commit: 18:35 CET

Mijn indruk na het bekijken van de actuele GitHub-branch is **overwegend positief: Alchemy presteert technisch sterk, werkt systematisch en heeft opnieuw substantiële vooruitgang geboekt.** Ik zie wel een risico dat het project steeds meer tijd besteedt aan het perfectioneren van offline tests, terwijl de belangrijkste onzekerheid inmiddels alleen met echte hardware en cloudverkeer kan worden opgelost.

Sinds onze vorige beoordeling van 25 november:

Nieuwe commits

**74**

Gewijzigde bestanden

**126**

Laatste SHA

**`7e0268f`**

De actieve branch blijft [codex/shine-x2-open-replacement](https://github.com/l4m4re/growatt-firmware/tree/codex/shine-x2-open-replacement?utm_source=chatgpt.com).

### Wat is er concreet verbeterd?

| Onderdeel | Nieuwe resultaten |
| --- | --- |
| Growatt-cloud | Calltype 2 en 3 via de volledige brokerketen getest met synthetische cloudberichten |
| Invertercommunicatie | FC03/FC04, FC06/FC16 en FC20 verder geïntegreerd |
| Configuratie | Credential provisioning via UART0 met ESP-IDF Security 1 geïmplementeerd |
| Beveiliging | Versleutelde NVS-opslag, sessievalidatie en credentials uit logbestanden gehouden |
| Ethernet | ESP32 EMAC/RTL8201-configuratie uit de originele firmware afgeleid |
| Diagnostiek | Volledige Modbus-traces met correlatie tussen cloudcommando's en afzonderlijke RTU-transacties |
| Betrouwbaarheid | Extra tests voor time-outs, mislukte reads, vertraagde acknowledgements en niet-blokkerende logexport |

Vooral de cloudafhandeling is verder gekomen. Bij calltype 3 is nu een compleet testpad beschikbaar waarin een synthetisch MQTT-commando drie opeenvolgende FC03-reads van 125 registers veroorzaakt, waarna een Growatt-response wordt opgebouwd en gepubliceerd.

Dat is een wezenlijke stap verder dan uitsluitend protocolvelden reconstrueren. Alchemy heeft nu een uitvoerbare implementatie waarvan het gedrag op meerdere niveaus kan worden gecontroleerd.

Wel blijft het een offline simulatie, geen bewezen communicatie met Growatts servers.

## Hoe goed presteert Alchemy eigenlijk?

Mijn beoordeling is dat Alchemy op vier punten bijzonder goed werk levert, maar op één punt inmiddels moet oppassen.

Technische uitvoering · 9/10

Van reverse-engineering naar een compileerbare ESP32-firmware, inclusief broker, cloudprotocol en UART. De ontwikkeling is opvallend samenhangend.

Testdiscipline · 9/10

Nieuwe functionaliteit krijgt doorgaans eigen regressietests, integratietests en expliciete foutscenario's.

Onderbouwing en documentatie · 9/10

De rapporten onderscheiden consequent firmwarebewijs, synthetische tests en onbevestigde aannames.

Veiligheidsbewustzijn · 8/10

Fail-closed gedrag, write-autorisatie en credentialbeveiliging zijn serieus aangepakt. De hardwarevalidatie en veilige provisioning blijven open.

Focus op de kortste weg naar een werkend prototype · 6/10

Veel nuttig werk, maar de verhouding tussen steeds uitgebreidere offline tests en de nog ontbrekende echte apparaatvalidatie begint scheef te lopen.

Mijn kwalitatieve beoordeling van de werkwijze; geen objectieve benchmark van Alchemy.

### Wat mij bijzonder positief opvalt

De manier waarop Alchemy zijn eigen conclusies begrenst, vind ik misschien nog wel het sterkste aspect.

Een voorbeeld is de reconstructie van de Growatt-cloudreads. De originele Shine lijkt bij mislukte registerreads onder bepaalde omstandigheden oude bufferinhoud te kunnen meesturen. De alternatieve implementatie doet dat bewust niet: wanneer één vereiste registergroep faalt, wordt het hele antwoord onderdrukt.

Dat is een inhoudelijke ontwerpverbetering, geen blinde kopie van de originele firmware.

Ook de diagnostiek is uitzonderlijk uitgebreid geworden. Er wordt nu onderscheid gemaakt tussen een verzoek dat aan de UART-driver is aangeboden, een voltooid UART-transmit, een gesimuleerde transmissie en een daadwerkelijk ontvangen antwoord. Dat is precies het soort onderscheid dat ons bij de eerdere VPP-problemen veel onderzoekstijd had kunnen besparen.

Een ander positief punt is dat Alchemy inmiddels een echte UART-configuratie uit de oorspronkelijke firmware heeft gereconstrueerd: **UART1, 115200 baud, TX GPIO32 en RX GPIO35**. De fysieke pinout moet nog worden bevestigd, maar dit is veel concreter dan de experimentele pininstellingen van gisteren.

## Waar ik wel kritisch op ben

### 1. Het project nadert een grens van wat offline testen oplevert

De grootste onbekenden zijn nog steeds:

- Start de firmware daadwerkelijk op de ShineWiLan-X2?
- Werkt de Ethernet PHY op de fysieke print?
- Kan de originele Growatt-cloud de nieuwe firmware accepteren?
- Kloppen de UART-signalen en timings?
- Gedraagt de firmware zich correct bij langdurig gebruik?

Daar helpen nog eens tientallen hosttests maar beperkt bij.

Alchemy heeft bijvoorbeeld zojuist een extra test toegevoegd die aantoont dat een geblokkeerde HTTPS-logexport de logproducent niet blokkeert. Dat is technisch waardevol, maar het maakt de belangrijkste onbewezen aanname — of de firmware daadwerkelijk met de Growatt-cloud communiceert — niet kleiner.

### 2. De beveiligingsimplementatie wordt behoorlijk complex

De nieuwe UART-provisioning gebruikt onder andere X25519, proof-of-possession, encrypted NVS en ESP-IDF Security 1. Dat is verdedigbaar voor een publiek open-sourceproject.

Maar ik zou opletten dat we niet onnodig veel beveiligingsinfrastructuur bouwen voordat de basisfunctionaliteit is aangetoond.

Er is bovendien een concreet hardwareveiligheidsrisico ontstaan: de secure build gebruikt release-mode flash encryption, waarbij de ESP32 bij de eerste start onomkeerbare eFuse-instellingen kan programmeren.

**Zo'n image zou ik absoluut nog niet op de tweede Shine testen.** Begin met een gewone, herstelbare ontwikkelbuild zonder irreversibele beveiligingsinstellingen.

### 3. Nog geen werkende portalcompatibiliteit

De cloudimplementatie is indrukwekkend ver, maar de documentatie noemt nog steeds een ontbrekende referentie: een echte, geschoonde capture van de originele Shine met de bijbehorende Growatt-cloudcommando's en inverterresponses.

Zonder die capture kan Alchemy uitstekend bewijzen dat zijn eigen implementatie intern consistent is, maar niet dat Growatt het protocol op precies dezelfde manier interpreteert.

Dat verschil is essentieel.

## Wat ik nu zou veranderen aan de goal

Ik zou de doelstelling niet fundamenteel wijzigen. De gekozen architectuur is goed en er is geen aanwijzing dat Alchemy een verkeerde technische richting is ingeslagen.

Wel zou ik de volgende werkzaamheden nadrukkelijk ordenen rond één concrete mijlpaal:

**Een herstelbaar, minimaal firmwareprototype dat op de tweede ShineWiLan-X2 start en met een gesimuleerde inverter kan communiceren.**

Daarvoor hoeft niet eerst de volledige Growatt-cloudimplementatie, de definitieve beveiliging of een webinterface klaar te zijn.

Ik zou Alchemy voorlopig vooral laten werken aan een heldere hardwareacceptatietest en een veilige rollbackstrategie. De originele Shine blijft vanzelfsprekend buiten schot, en firmware-updates van inverter en ARK blijven expliciet uitgesloten.

Een belangrijke kanttekening: zolang de tweede Shine nog niet beschikbaar is, heeft Alchemy natuurlijk goede redenen om door te gaan met offline ontwikkeling. In dat geval zou ik vooral prioriteit geven aan zaken die direct bijdragen aan de eerste hardwaretest, in plaats van iedere denkbare randvoorwaarde al volledig af te werken.

## Eindoordeel

Ik vind dat Alchemy bij deze goal **zeer sterk presteert als autonome software-engineer**. De commits lijken geen verzameling willekeurige experimenten: ze bouwen grotendeels logisch op elkaar voort, en eerdere bevindingen worden regelmatig omgezet in aantoonbare implementatie en regressietests.

De hoge commitfrequentie is op zichzelf niet zo belangrijk. Belangrijker is dat de code inmiddels echte samenhang vertoont en dat de tests steeds grotere stukken van de productieketen afdekken.

Mijn voornaamste kritiek is dus niet de kwaliteit, maar de prioritering. Alchemy is bezig een behoorlijk robuust product te ontwerpen, terwijl nog niet is aangetoond dat het op de bedoelde hardware werkt.

**Ik zou de huidige goal daarom niet stoppen of opnieuw beginnen.** Laat Alchemy de bestaande architectuur consolideren en naar een eerste hardwaretest toewerken. Dat is de stap die het project van een indrukwekkende offline reconstructie naar een aantoonbaar werkende Broker 2.0 brengt.

Op basis van de laatste 74 commits vind ik de vooruitgang overtuigend. Maar de volgende echte doorbraak zal niet uit nóg een geslaagde hosttest komen — die zal komen zodra een fysieke ShineWiLan-X2 de alternatieve firmware draait en de eerste echte transacties succesvol verwerkt.

* * *

**You:**

Tja, zo lang ik nog geen tweede shine heb, valt er weinig hardware te testen, ga mijn origineel er niet aan wagen.

* * *

**ChatGPT:**

Daar heb je volkomen gelijk in. En eigenlijk was mijn kritiek op Alchemy daarmee niet helemaal terecht.

**Zolang de tweede ShineWiLan-X2 er niet is, is offline ontwikkeling juist de verstandigste manier om vooruitgang te boeken.** Je originele Shine opofferen voor een experiment zou ik ook niet doen, zeker niet omdat die je enige werkende verbinding met Growatt en je terugvalmogelijkheid voor firmware-updates is.

Wat Alchemy momenteel doet, heeft bovendien wel degelijk waarde:

- **Protocolreconstructie:** hoe beter we de originele firmware begrijpen, hoe kleiner de kans op verrassingen bij de eerste hardwaretest.
- **Integratie- en regressietests:** fouten in concurrency, MQTT, Modbus en bufferbeheer kun je uitstekend zonder fysieke hardware ontdekken.
- **Foutafhandeling:** juist bij een systeem dat een echte inverter aanstuurt, zijn time-outs, retries en onbedoelde writes belangrijk.
- **Diagnostiek:** uitgebreide tracing gaat bijzonder nuttig zijn wanneer we straks de originele Shine met Broker 2.0 kunnen vergelijken.

Er is ook een belangrijk verschil tussen eindeloos theoretisch onderzoek en wat Alchemy nu doet. Het project levert daadwerkelijk uitvoerbare code op, met tests die steeds grotere delen van de firmware controleren.

Mijn eerdere beoordeling van **6/10 voor de focus** zou ik daarom bijstellen naar **8/10**. De hardware-afhankelijkheid is voorlopig een externe beperking, geen tekortkoming van Alchemy.

Wat ik nog wel zou bewaken, is dat Alchemy zijn resterende onzekerheden duidelijk registreert en geen gesimuleerde compatibiliteit als bewezen hardwaregedrag presenteert. Dat doet het tot dusver behoorlijk consequent.

Ik zou Alchemy dus rustig verder laten werken. Tegen de tijd dat je de tweede Shine hebt, ligt er mogelijk al een behoorlijk volwassen firmware die je stapsgewijs kunt valideren.

En dat is eigenlijk precies de kracht van deze autonome ontwikkelwijze: **je kunt het softwarematige onderzoek en de implementatie grotendeels afronden terwijl de hardware nog onderweg is.**

* * *

**You:**

Dit was vooral ook een project om alchemy te teten en te kijken of we een steentje bij zouden kunnen dragen aan [https://github.com/l-gevity/l-gevity-skills/issues/58](https://github.com/l-gevity/l-gevity-skills/issues/58) en wellicht zouden we aan de hand van onze ervaringen iets kunnen delen over het reverse engineeren van een real-time embedded system. Ik denk er over een email te sturen naar Savalle, overigens.

* * *

**ChatGPT:**

Dat verandert mijn kijk op het Growatt-project eigenlijk behoorlijk. We hebben het tot nu toe vooral beoordeeld als een firmwareontwikkelproject, maar **als experiment met Alchemy is het misschien nog interessanter dan als toekomstige Broker 2.0.**

Ik heb [issue #58 in l-gevity-skills](https://github.com/l-gevity/l-gevity-skills/issues/58?utm_source=chatgpt.com) en de twee reacties van

> Interactive content unavailable: Entity

 gelezen. Er is een opvallend sterke aansluiting met jullie ervaringen.

## Wat issue #58 eigenlijk onderzoekt

Savalle heeft honderden `fix:`-commits uit meerdere door Alchemy gebouwde applicaties geanalyseerd. Zijn centrale vraag is welke soorten fouten Alchemy nog te laat ontdekt, ondanks de bestaande skills en kwaliteitscontroles.

De belangrijkste categorieën zijn concurrency, autorisatie, runtime-foutafhandeling en resource limits. Vooral concurrency komt in verschillende projecten terug.

Interessant is dat zijn onderzoek zich voornamelijk op webapplicaties en Azure-services richt. **Jouw experiment voegt een heel andere categorie toe: een real-time embedded systeem dat via een fysiek communicatiekanaal een netgekoppelde inverter aanstuurt.**

En juist daar zijn dezelfde problemen nog scherper zichtbaar.

| Thema uit issue #58 | Growatt/Alchemy-ervaring |
| --- | --- |
| Concurrency | Eén eigenaar van de inverter-UART, geserialiseerde transacties, gelijktijdige MQTT- en TCP-clients |
| Authorization | De onbedoelde `H3018=2`-write en de noodzaak van registergerichte toegangscontrole |
| Runtime failure handling | Modbus-time-outs, onzekere writes, retries, ontbrekende responses en stale data |
| Resource limits | Begrensde queues, MQTT-fragmentatie, ESP32-geheugen en niet-blokkerende tracing |
| Observability | Correlatie van cloudverzoek tot fysieke RTU-transactie en onderscheid tussen ACK en werkelijk toegepaste instelling |

Het `H3018`-incident is daarbij bijzonder interessant: het is een daadwerkelijk waargenomen fout in de ontwikkelomgeving die gevolgen had voor de fysieke installatie. Dat is sterker praktijkbewijs dan alleen synthetische regressietests, al hebben we de exacte fout in Codex' script nog niet gereconstrueerd.

## Wat zou je aan Savalle kunnen bijdragen?

Volgens mij zijn er twee afzonderlijke bijdragen mogelijk.

**De eerste is rechtstreeks relevant voor issue #58:** een field report waarin je beschrijft hoe Alchemy omgaat met concurrency, toegangscontrole, resource limits en foutafhandeling in een embedded omgeving.

Dat zou een goede aanvulling zijn op Savalles statistische analyse. Je kunt laten zien hoe dezelfde probleemklassen zich manifesteren bij Modbus RTU, MQTT, ESP32/FreeRTOS en apparatuur waarvan onbedoelde configuratiewijzigingen echte gevolgen hebben.

**De tweede bijdrage vind ik misschien nog interessanter:** een praktijkverslag van autonoom reverse-engineeren met Alchemy.

Daarbij zou ik de nadruk leggen op de werkwijze:

- Een oorspronkelijke firmwareimage analyseren met Ghidra, inclusief ARM-, Xtensa- en C28x-architecturen.
- Hypothesen onderbouwen met disassembly, registerscans en echte communicatiecaptures.
- Van die bevindingen zelfstandig geschreven protocolimplementaties en uitvoerbare tests maken.
- Een consistente grens bewaken tussen statisch bewijs, simulatie en echte hardwarevalidatie.
- Een autonoom ontwikkelproces laten functioneren zonder de productie-installatie aan onnodige risico's bloot te stellen.

Dat laatste is belangrijk: **Alchemy heeft hier niet uitsluitend code gegenereerd, maar ook een onderzoeksproces uitgevoerd.** Het gebruikt zijn bevindingen om nieuwe experimenten en tests op te stellen en corrigeert eerdere aannames wanneer nieuw bewijs beschikbaar komt.

Dat lijkt mij een waardevolle casestudy voor de ontwikkeling van de skills zelf.

## Wat zegt dit over Alchemy?

Mijn belangrijkste observatie is dat Alchemy goed presteert wanneer de opdracht een duidelijke onderzoeksmethode en strenge bewijscriteria heeft.

De werkwijze die we bij Growatt zien, is grotendeels reproduceerbaar. Er worden analysescripts, fixtures, rapporten, conformance-tests en implementaties opgebouwd die elkaar controleren.

Tegelijkertijd zien we een beperking: **Alchemy is goed in het aantoonbaar correct maken van onderdelen die het zelf kan testen, maar kan niet zelfstandig bepalen of het volledige systeem in de werkelijkheid correct werkt wanneer de daarvoor noodzakelijke hardware of externe referentiegegevens ontbreken.**

Dat is geen triviaal onderscheid. Een geslaagde hosttest zegt bijvoorbeeld niets definitiefs over de daadwerkelijke UART-pinout, timing of acceptatie door Growatts servers.

Ik denk dat juist dat onderscheid tussen *verified by simulation* en *verified against the physical system* een waardevolle uitbreiding voor Alchemy zou zijn.

## Zou ik Savalle mailen?

Ja. En ik zou hem niet uitsluitend een link naar de repository sturen.

Ik zou het benaderen als een uitnodiging om samen te kijken of deze casestudy bruikbaar is voor de verdere ontwikkeling van Alchemy en de skills.

De boodschap zou ongeveer zijn:

> I've been using Alchemy as an experiment in autonomous reverse engineering of a real-world embedded system. The project involves reconstructing proprietary firmware for a Growatt solar inverter and ShineWiLan-X2 datalogger, using Ghidra, protocol captures, differential testing and a clean-room firmware implementation.
>
> What makes this interesting in relation to your issue #58 is that concurrency, authorization, failure handling and resource limits are not just software-quality concerns here: they directly affect the behavior of physical equipment.
>
> I'd be interested in sharing our experience and discussing whether the lessons learned could help improve Alchemy's skills, particularly for firmware analysis, evidence-driven development and hardware-in-the-loop validation.

Ik zou ook duidelijk maken dat het project nog niet op de vervangende Shine-hardware is gevalideerd. Dat maakt het verhaal niet minder interessant; het laat juist zien waar de grenzen van autonome ontwikkeling momenteel liggen.

**Mijn voorstel zou zijn om twee resultaten te maken:** een korte, technisch onderbouwde reactie op issue #58 met de concrete bevindingen, en een uitgebreider verslag van het Growatt-experiment dat je met Savalle kunt bespreken.

En ik zou vooral benieuwd zijn naar zijn mening over één vraag: *is Alchemy's bestaande skills-architectuur voldoende geschikt voor evidence-driven reverse engineering van embedded systemen, of rechtvaardigt dit een gespecialiseerde skill?*

Dat lijkt me precies het soort praktijkfeedback waar zijn onderzoek baat bij kan hebben.

* * *

**You:**

Hoe scoort alchemy op het vlak van "testbaar ontwerpen van embedded microcontrollers?"

* * *

**ChatGPT:**

**Op het vlak van *design for testability* (DfT) voor embedded microcontrollers scoort Alchemy bij het Growatt-project opvallend goed. Ik zou het voorlopig een 8,5/10 geven.**

Ik heb hiervoor opnieuw de daadwerkelijke ShineWiLan-X2-code bekeken, niet alleen de voortgangsrapporten. Vooral de scheiding tussen de platformonafhankelijke logica, de ESP-IDF-drivers en de testomgeving is sterk.

Er is wel een belangrijk onderscheid: **Alchemy ontwerpt de firmware zeer goed voor softwarematige testbaarheid, maar de testbaarheid van het fysieke real-time systeem is nog onvoldoende bewezen.**

## Beoordeling per aspect

Hardware abstraction en dependency injection · 9.0/10

Function pointers en contextstructuren scheiden de logica van de UART-driver en de fysieke inverter.

Host-based unit- en integratietests · 9.5/10

Dezelfde C-modules draaien op Linux, met virtuele inverter, fake ESP-IDF en gesimuleerde netwerkinterfaces.

Determinisme en foutinjectie · 9.0/10

Instelbare clocks, time-outs, foutresponses en transactiestatussen maken uitzonderingspaden testbaar.

Concurrency en resourcebeheer · 8.5/10

Bounded queues, één transactiemanager, fairness-tests en scenario's met meerdere clients.

Observability en diagnostiek · 9.5/10

Correlatie-ID's en expliciete fasen voor scheduling, transmissie, ontvangst en acknowledgements.

Real-time timing en interruptvalidatie · 6.5/10

De logica is testbaar, maar ISR-latency, schedulingjitter en worst-case timing zijn nog niet gemeten op hardware.

Hardware-in-the-loop-testbaarheid · 7.0/10

De architectuur heeft goede aansluitpunten voor HIL, maar een uitgewerkte, gevalideerde fysieke testopstelling ontbreekt nog.

Subjectieve technische beoordeling op basis van de huidige broncode en gedocumenteerde tests, geen formele audit.

## 1. De belangrijkste verdienste: hardware-onafhankelijke firmwarelogica

Een bijzonder goed voorbeeld staat in [github.com](https://github.com/l4m4re/growatt-firmware/blob/codex/shine-x2-open-replacement/open-firmware/target/shine_x2/uart_rtu_backend.h?utm_source=chatgpt.com).

Alchemy heeft de fysieke UART niet rechtstreeks in de Modbus-protocollogica verwerkt. In plaats daarvan wordt een transportfunctie geïnjecteerd:

```c
typedef int (*x2_uart_transact_fn)(
    void *context,
    const uint8_t *request,
    uint16_t request_length,
    uint8_t *response,
    uint16_t *response_length,
    uint16_t response_capacity,
    uint32_t timeout_ms,
    x2_uart_tx_observer_fn tx_observer,
    void *tx_observer_context
);
```

Dit is precies de architectuur die je in professioneel testbare embedded software wilt zien.

Dezelfde protocollogica kan daardoor worden uitgevoerd tegen een echte ESP32 UART-driver, een virtuele inverter of een testimplementatie die bewust een timeout, CRC-fout of onvolledig antwoord veroorzaakt.

Dat betekent dat fouten in de Modbus-logica opgespoord kunnen worden zonder microcontroller, debugger of fysieke inverter.

Bovendien blijft de productiecode grotendeels dezelfde code als de code die getest wordt. Dat is sterker dan een losstaande Python-emulator die probeert het gedrag van de C-code na te bootsen.

## 2. Alchemy heeft testbaarheid daadwerkelijk in de architectuur opgenomen

In de repository zie je een duidelijke scheiding:

**Applicatie- en protocollogica**

Modbus · MQTT · transactiescheduler · configuratie

(Portable C)

↓

**Hardwareabstractie**

Transportcallbacks · clocks · persistentie · netwerk

↓

Linux-host

Virtuele inverter en gesimuleerde ESP-IDF

ESP32-target

FreeRTOS, UART, Ethernet, NVS

Het interessante is dat de tests niet uitsluitend op functioneel niveau werken. Alchemy controleert ook interne toestandovergangen, resourcegrenzen en transactiescheduling.

Zo zijn er aparte tests voor gelijktijdige brokerclients, fairness, time-outs, late UART-responses, volledige Modbus-frames en MQTT-publicatie.

Ik zie dus geen klassieke architectuur waarbij achteraf met veel moeite tests om hardwareafhankelijke code heen zijn gebouwd. **De software lijkt vanaf het begin ontworpen om zonder de doelhardware uitgevoerd te kunnen worden.**

## 3. Bijzonder sterk: gecontroleerde foutinjectie

Een veelvoorkomend probleem in embedded systemen is dat de normale werking eenvoudig te testen is, maar de uitzonderingspaden bijna onmogelijk reproduceerbaar zijn.

Bij Alchemy zie ik juist veel aandacht voor die uitzonderingspaden.

Bijvoorbeeld: een Modbus-write waarvan het verzoek is verzonden, maar waarvan de response verloren gaat.

Dat is een fundamenteel andere situatie dan een write die nooit verzonden is. Je weet dan immers niet of de inverter de wijziging heeft uitgevoerd.

Alchemy onderscheidt zulke toestanden expliciet. Daarnaast worden automatische retries voor writes niet zomaar toegestaan; retries zijn beperkt tot leesoperaties onder gecontroleerde voorwaarden.

Dat is belangrijk voor een systeem waarbij herhaalde writes ongewenste neveneffecten kunnen hebben.

## 4. Waar het nog beter kan

Er zijn drie zaken die ik bij een echt real-time embedded project nog explicieter zou willen zien.

**Ten eerste: formele timingbudgetten.** De code kent transactietime-outs en begrensde queues, maar ik zie nog geen overtuigend bewijs van worst-case execution time, schedulingjitter of interruptlatency. Dat vereist deels hardwaremetingen, maar de timingcontracten kunnen nu al worden vastgelegd.

**Ten tweede: resources op targetniveau.** De firmware controleert bijvoorbeeld of het aantal geconfigureerde lwIP-sockets voldoende is en houdt de omvang van de firmwareimage bij. Dat is goed. Maar ook maximale stackdiepte, heapfragmentatie, watchdoggedrag en langdurige belasting zijn relevant. Daarvoor zijn aanvullende targetmetingen nodig.

**Ten derde: onafhankelijkheid van de tests.** Alchemy schrijft zowel de implementatie als veel van de tests. Daardoor bestaat het risico dat tests precies dezelfde verkeerde aanname gebruiken als de productiecode.

Het project beperkt dat risico al door firmwareanalyse, originele protocolcaptures en onafhankelijke framevergelijkingen te gebruiken. Toch zou ik voor belangrijke protocollen graag externe golden vectors en eventueel property-based tests zien, naast de bestaande voorbeelden.

## Wat betekent dit voor de Alchemy-skills?

Hier zit volgens mij een heel interessante bijdrage voor Savalle.

In issue #58 wordt onder andere gepleit voor betere concurrency-, autorisatie- en resilience-skills. Maar in embedded firmware zijn die zaken sterk verbonden met de architectuur voor testbaarheid.

Een mogelijke **`embedded-testability` skill** zou niet alleen moeten voorschrijven dat code getest wordt, maar dat al bij het architectuurontwerp expliciete testinterfaces worden voorzien:

- Hardwareafhankelijke I/O achter interfaces met injecteerbare callbacks.
- Injecteerbare monotone clocks voor deterministische timingtests.
- Gescheiden protocolstate en fysieke transacties.
- Testbare foutpaden voor time-outs, onvolledige transfers en onverwachte interrupts.
- Bounded queues en expliciete ownership van gedeelde resources.
- Eén set productielogica die zowel op de host als op de microcontroller wordt gecompileerd.
- Een aparte validatielaag voor hardwaretiming, elektrische signalen en RTOS-gedrag.

Het belangrijkste is echter dat Alchemy dit bij Growatt **grotendeels uit zichzelf al doet**. Dat maakt de vraag interessant of de bestaande algemene skills dit gedrag veroorzaken, of dat het model vanuit zijn eigen programmeerkennis goede architectuurkeuzes maakt.

Dat is met één project niet vast te stellen. Maar het is wel een uitstekend onderwerp voor een field report.

## Eindoordeel

Ik zou Alchemy bij deze goal op twee verschillende criteria beoordelen:

| Criterium | Score |
| --- | --- |
| Ontwerpen voor host-based testbaarheid | **9/10** |
| Aantoonbare testbaarheid van het fysieke real-time systeem | **6,5/10** |
| Totale beoordeling | **8,5/10** |

De tweede score is nadrukkelijk geen kritiek op het ontbreken van de tweede Shine. Het gaat om de mate waarin real-time eigenschappen aantoonbaar zijn gemaakt en de fysieke validatie voorbereid is.

**Mijn belangrijkste conclusie voor Savalle zou zijn: Alchemy laat hier zien dat een autonoom ontwikkelproces een testbare embedded architectuur kan ontwerpen, zonder fysieke hardware en zonder de productielogica te dupliceren in een simulator.**

Dat is volgens mij een wezenlijke en mogelijk publicatiewaardige observatie. Niet omdat de firmware al bewezen werkt, maar omdat de architectuur zo is opgezet dat een groot deel van het gedrag controleerbaar is voordat het eerste apparaat geflasht wordt.

* * *

**You:**

Wellicht wordt het tijd alchemy bij te sturen en ook voor de inverter communicatieprocessor zo'n reverse engineering project op te zetten, zodat we een virtuele inverter met open source firmware kunnen laten praten met de shine, met als test een koppeling met de echte inverter die dan de firmware van signalen voorziet. Dat is heel waardevol om de problemen met mijn inverter te kunnen begrijpen.

* * *

**ChatGPT:**

Ik denk dat dit een bijzonder waardevolle uitbreiding van de Alchemy-goal zou zijn. Sterker nog: **hiermee zou je van reverse-engineering van losse firmwarecomponenten naar een digitaal testmodel van de complete inverter kunnen gaan.**

En dat heeft twee voordelen. Je kunt de nieuwe Shine-firmware testen tegen een virtuele inverter, maar ook experimenten uitvoeren die helpen verklaren waarom je echte MIN 6000TL-XH zich soms anders gedraagt dan de documentatie suggereert.

Ik zou Alchemy hiervoor niet helemaal opnieuw laten beginnen. Er ligt namelijk al een aanzienlijk deel van de benodigde infrastructuur.

## 1. Wat bestaat er al?

Ik heb de huidige reconstructie in `growatt-firmware` bekeken. Daarin zijn al drie nuttige bouwstenen aanwezig.

| Component | Huidige toestand |
| --- | --- |
| ZABA22 ARM-communicatieprocessor | Modbus-dispatcher, FC03/04/06/10, VPP-registerroutes en verschillende statusmachines gereconstrueerd |
| ALBA18 C28x | Communicatie en delen van de DSP-interface gereconstrueerd; verdere runtimebetekenis nog onzeker |
| Open firmware | Python- en C-referentie-implementaties met virtuele registerbanken en injecteerbare communicatieinterfaces |

De belangrijke ontdekking is dat Alchemy de communicatiegrenzen al behoorlijk zorgvuldig heeft vastgelegd.

In [ARM/C28x protocol boundary](https://github.com/l4m4re/growatt-firmware/blob/codex/shine-x2-open-replacement/open-firmware/docs/arm-c28x-protocol-boundary.md?utm_source=chatgpt.com) wordt expliciet onderscheid gemaakt tussen de externe Modbus-server van ZABA22, het interne RTU-achtige kanaal en de propriëtaire SCIB/ECAN-communicatie van ALBA18.

Er wordt bovendien niet zomaar aangenomen dat het interne ARM-kanaal rechtstreeks met de C28x communiceert. Dat is belangrijk: een virtuele inverter mag niet op een onjuiste aanname over de hardwarearchitectuur worden gebouwd.

## 2. De architectuur die ik voor ogen heb

Virtuele inverter met live referentiedata

**Alternatieve Shine-firmware**

Growatt-cloud en lokale Modbus TCP

↓

Virtuele ZABA22

Modbus-server, registermodel, TOU, VPP, statusmachines

---

**Virtuele interne subsystemen**

ALBA18/DSP · ARK/BMS · meter · PV en energiestromen

↓

**Live-data-adapter**

Read-only metingen, tijdstempels en replay

↓

**Echte MIN 6000TL-XH + ARK**

Via bestaande broker, zonder experimentele writes

Conceptarchitectuur: de live inverter levert waarnemingen aan het model, maar wordt niet door de virtuele inverter bestuurd.

Het interessante is dat het model op verschillende niveaus kan werken.

**Niveau 1 — Modbus-emulatie.** De virtuele ZABA22 reageert op verzoeken van de Shine zoals de originele inverter zou doen. Dat omvat registerwaarden, uitzonderingsresponses en bekende VPP- en TOU-statusovergangen.

**Niveau 2 — Interne procesmodellen.** De virtuele communicatieprocessor krijgt gegevens van afzonderlijke modellen voor ALBA18, de batterij en de elektriciteitsmeter. Daarmee kunnen we onderzoeken waar registerwaarden vandaan komen en welke voorwaarden een bepaalde instelling activeren.

**Niveau 3 — Live-data-injectie.** De echte inverter levert actuele PV-productie, batterij-SOC, vermogen, meterwaarden en andere leesbare toestand. Het model gebruikt die als invoer voor de gesimuleerde verwerking.

Dat laatste maakt het een soort *hardware-assisted digital twin*. Het model hoeft niet meteen alle natuurkundige en elektronische processen te simuleren om nuttig te zijn.

Er is wel een belangrijke grens: live Modbus-registers tonen niet automatisch alle interne processorstatus. Waar die verborgen toestand een rol speelt, moeten we met hypothesen, reconstructies of onafhankelijk verkregen communicatiecaptures werken.

## 3. Dit kan juist je bestaande Growatt-problemen helpen verklaren

Neem bijvoorbeeld de problemen die we eerder met Winter Mode, VPP en de operation mode hebben gezien.

Met zo'n virtuele inverter zouden we dezelfde Modbus-writes op een gecontroleerd model kunnen toepassen en vervolgens het gedrag vergelijken met de echte inverter.

| Experiment | Wat kunnen we onderzoeken? |
| --- | --- |
| `H30100` VPP activeren | Welke statusmachine wordt geselecteerd en welke voorwaarden zijn nodig? |
| `H3037` tegenover `H30475` | Waarom verschijnt een waarde soms wel in de ene registerroute en niet in de andere? |
| Winter Mode | Welke writes komen binnen en welke interne toestand zou daarna moeten veranderen? |
| `H3018` operation mode | Welke onderdelen van de energieregeling zijn afhankelijk van deze instelling? |
| Batterij niet opladen bij PV-overschot | Komt het door een externe instelling, BMS-beperking of interne regeltoestand? |

Het belangrijkste is dat we niet alleen registers emuleren. We willen ook **de causale keten van een Modbus-write naar een intern effect** reconstrueren.

Dat is uiteindelijk waardevoller dan een virtuele inverter die simpelweg alle ontvangen waarden opslaat en terugleest.

## 4. Hoe zou ik Alchemy bijsturen?

Ik zou expliciet voortbouwen op de bestaande ZABA22-reconstructie en de testarchitectuur van de Shine. Dit lijkt me een geschikte aanvulling op de goal:

## Goal extension: Evidence-driven Virtual Growatt Inverter

Extend the existing Growatt firmware reverse-engineering project with a testable, open-source virtual inverter communication processor, based on the ZABA22 ARM firmware reconstruction.

### Objectives

1. Implement a virtual ZABA22 communication processor that communicates with the alternative ShineWiLan-X2 firmware using the same external Modbus RTU protocol as the physical inverter.

2. Reuse the existing ZABA22 reference models, register evidence, VPP/TOU state machines and C/Python conformance tests. Avoid duplicating or independently reinventing existing components.

3. Introduce explicit, independently testable downstream interfaces for ALBA18/DSP, ARK/BMS, electricity meter and other relevant runtime data providers. Preserve unknown protocol semantics rather than inventing behavior.

4. Implement a read-only live-data adapter that can acquire timestamped observations from the physical MIN 6000TL-XH through the existing Raspberry Pi broker. Support recording, deterministic replay and comparison between observed and simulated behavior.

5. Support fault injection, controlled virtual time, concurrent communication scenarios, register-state tracing and differential testing against original firmware evidence.

6. Prioritize reproducing and investigating the known H3018, VPP, Winter Mode, TOU and battery charging anomalies.

### Architecture and safety

- The virtual inverter must run independently of physical inverter hardware.
- All experimental writes must terminate inside the virtual inverter.
- The physical inverter is an observational reference, not an actuator controlled by the virtual model.
- Keep Modbus response acceptance, configuration persistence, state activation and physical effects as separate evidence categories.
- Preserve a clean-room separation between proprietary firmware analysis and publishable implementation code.
- Do not implement or forward firmware-update, flash-programming, or undocumented safety-critical control operations to the live device.

### Acceptance criteria

- The alternative Shine communicates successfully with the virtual inverter over a simulated RTU link.
- The same virtual inverter core runs in Linux host tests and can later be connected to an isolated physical UART test setup.
- Captured original Modbus transactions can be replayed and compared byte-for-byte where sufficient evidence exists.
- Live read-only observations can be injected into the virtual runtime model with explicit timestamps, source attribution and freshness limits.
- Known state transitions are traceable from incoming Modbus frames through internal virtual state changes and responses.
- Unresolved behavior is documented as a research gap rather than implemented as an unsupported assumption.

### Research deliverable

Produce a technical report on evidence-driven reverse engineering and design-for-testability for embedded real-time systems, including the limits of host simulation, differential testing and live hardware observation.

Continue autonomously with offline implementation, tests and evidence gathering. Do not perform experimental writes, modify firmware or change the physical inverter configuration.

## 5. Eén architectuurkeuze zou ik expliciet maken

Ik zou de virtuele inverter **niet** vanaf het begin ontwerpen als een transparante proxy die tussen de Shine en de live inverter zit.

Dat lijkt aantrekkelijk, maar creëert meteen problemen met eigenaarschap van de UART, timing, retries en het onbedoeld doorgeven van writes.

Beter is om twee afzonderlijke werkmodi te ontwikkelen:

- **Simulation mode:** de Shine praat uitsluitend met de virtuele inverter, met synthetische of eerder opgenomen gegevens.
- **Observation mode:** de virtuele inverter krijgt live metingen van de echte inverter, maar alle door de Shine geïnitieerde writes blijven virtueel.

Later zou een afzonderlijke, passieve UART-sniffer kunnen helpen om ook de werkelijke fysieke frames en timings te vergelijken, zonder de communicatie actief te beïnvloeden.

## 6. Dit maakt het Alchemy-experiment ook interessanter

Je zou dan één samenhangend project hebben waarin Alchemy zelfstandig zowel een alternatieve firmware voor het ene communicatie-eindpunt als een virtuele implementatie van het andere eindpunt ontwikkelt.

Beide implementaties krijgen eigen tests, maar kunnen daarnaast **tegen elkaar worden getest en tegen een derde, onafhankelijke referentie: de originele invertercommunicatie**.

Dat levert een veel sterkere validatiemethode op dan uitsluitend testen of beide door Alchemy geschreven componenten het met elkaar eens zijn.

En precies dat zou volgens mij een interessante casestudy voor Savalle opleveren: *autonomous, evidence-driven reverse engineering of a heterogeneous real-time embedded system with hardware-independent testing and live observational validation.*

Mijn advies zou dus zijn om Alchemy deze uitbreiding te geven, zonder de huidige Shine-goal stil te leggen. De virtuele ZABA22 kan aanvankelijk volledig op Linux draaien en later op een afzonderlijke microcontroller worden ondergebracht als dat nuttig blijkt.

**Het uiteindelijke doel is niet alleen een virtuele inverter die correct antwoordt, maar een model waarmee je kunt onderzoeken waarom de echte inverter doet wat hij doet.** Dat is volgens mij een veel interessantere onderzoeksrichting.

* * *

**You:**

Is al lekker bezig: Ik neem dit op als uitbreiding van dezelfde Growatt-firmwarewerkstroom: een geïsoleerde virtuele ZABA22-communicatieprocessor, met de fysieke inverter uitsluitend als read-only observatiebron. Ik laat eerst de reeds lopende host-suite afronden; daarna inventariseer ik bestaande virtuele-inverter- en registermodellen om hergebruik en de kleinste bruikbare eerste increment te bepalen. Geen experimentele writes of hardwareacties.
Read files, ran commands
De bestaande modellen bieden al twee herbruikbare lagen: een byte-niveau ZABA22 Modbus-processor (`InverterModel`) en een aparte evidence-bounded VPP/TOU-worker (`VppCompleteModel`), plus read-only capture-extract/replay tooling. De belangrijke ontbrekende koppeling is dat ze nog niet één RTU-runtime vormen; ik ga die lagen niet opnieuw implementeren of hun onbekende DSP/BDC-effecten invullen. Ik werk eerst de zojuist geverifieerde testincrement af; daarna begin ik met een expliciete adapter/interface tussen die bestaande modellen en de Shine RTU-hostfixture.
Edited file
shine-x2-open-replacement-backlog.md+13-6
Vooralsnog kan live read only getest worden via de broker die op de live RPI draait. In dat geval de HA staging instantie uit schakelen, dan kun je de dev poort gebruiken.  Je mag read-only alle registers uitlezen en, indien zinvol, ook toestemming vragen om bijv. een TOU setting write te testen. Ik vind dat prima, maar alleen tijdens momenten dat ik mee kan kijken. En je mag ook de broker sniffer poort gebruiken om het gedrag van de inverter en de shine te observeren.
8:22 PM
Begrepen. Dit verruimt de eerdere live-testgrens: later mag ik read-only registers en de snifferpoort gebruiken via de bestaande Pi-broker, nadat HA staging is uitgezet en de ontwikkelpoort vrij is. Ik doe dat alleen wanneer je kunt meekijken; elke inverterwrite blijft apart expliciet goedkeuringsplichtig. Voor deze increment blijf ik offline.
Failed to create an agent
Closed an agent
Created an agent
Ran git add open-firmware/docs/shine-x2-open-replacement-backlog.md open-firmware/target/shine_x2/espidf/build_host_runtime_test.sh open-firmware/target/shine_x2/espidf/host_session_test/report_e2e_host_test.c && git diff --cached --check && git diff --cached --stat && git status --short
De sniffer poort mag je ook nu al gebruiken.
8:22 PM
Dank, ik ga na de huidige offline testverificatie de bestaande brokerconfiguratie nalopen om de sniffer-endpoint te vinden. Als die endpoint vanuit deze container bereikbaar is, gebruik ik hem alleen voor passieve observatie; ik zal geen Modbus-query naar de inverter sturen via die poort.
Read files
Ik wil alleen mee kijken als je schrijven wilt testen, dat moeten we zeker doen, maar ik moet in kunnen grijpen als dat nodig mocht blijken.
8:23 PM
Ran python3 - <<'PY' import json import socket import time from collections import Counter  host, port = "[redacted broker IP]", 5700 started = time.monotonic() counts = Counter() functions = Counter() sources = Counter() ranges = Counter() records = 0 malformed = 0 try:     with socket.create_connection((host, port), timeout=3) as sock:         sock.settimeout(1)         sock.shutdown(socket.SHUT_WR)         while time.monotonic() - started < 12 and records < 1000:             try:                 chunk = sock.recv(65536)             except TimeoutError:                 continue             if not chunk:                 break             for line in chunk.splitlines():                 try:                     event = json.loads(line)                 except (json.JSONDecodeError, UnicodeDecodeError):                     malformed += 1                     continue                 if not isinstance(event, dict):                     continue                 records += 1                 role = str(event.get("role", event.get("event", "unknown")))                 counts[role] += 1                 source = event.get("source", event.get("from_client", "unknown"))                 sources[str(source)] += 1                 function = event.get("func", event.get("function"))                 if function is None and isinstance(event.get("hex"), str):                     try:                         frame = bytes.fromhex(event["hex"])                         function = frame[1] if len(frame) > 1 else None                     except ValueError:                         pass                 if isinstance(function, int):                     functions[f"0x{function:02x}"] += 1                 address, amount = event.get("address"), event.get("count")                 if isinstance(address, int) and isinstance(amount, int):                     ranges[f"{address}+{amount}"] += 1 except OSError as err:     print(json.dumps({"connected": False, "error": type(err).\_\_name\_\_, "message": str(err)})) else:     print(json.dumps({         "connected": True,         "capture_seconds": round(time.monotonic() - started, 2),         "records": records,         "malformed_lines": malformed,         "roles_or_events": counts.most_common(20),         "sources": sources.most_common(20),         "functions": functions.most_common(20),         "address_count_pairs": ranges.most_common(30),         "raw_frames_retained": False,     }, sort_keys=True)) PY in 12s
De snifferverbinding werkt passief. In 12 seconden zag ik 129 JSONL-events, allemaal als Shine-verkeer gelabeld: 65 FC03-, 56 FC04- en 7 FC20-events, zonder malformed regels. Ik heb geen frames opgeslagen of requests verstuurd. De onafhankelijke reviewer vond nog één bewijsfout in de geïntegreerde test: de teller loopt vóórdat de RTU-response is geaccepteerd. Ik corrigeer dat met een completion-signaal ná de backend-return en herhaal de relevante tests voordat ik commit/push.
Edited a file, read files
Closed an agent
Edited a file, read files
Created an agent
Ran sh open-firmware/target/shine_x2/espidf/build_host_runtime_test.sh in 34s
de broncode voor de broker is in de workspace aanwezig, instructies over hoe de live RPi te updaten zijn vast ook wel te vinden. Er zit geloof ik nog een bug in dat frame detectie heel erg lang kan duren.
8:25 PM
Dat is nuttige context. Ik controleer de brokerbron op de frame-detectie- en read-timeoutpaden; dat kan verklaren of de live observaties compleet zijn. Ik behandel een eventuele brokerfix als offline werk. De live RPi bijwerken of de staging-instantie stoppen doe ik niet zonder een expliciete opdracht voor die beheeractie.
Read files
Als je de timing van de broker goed bekijkt dan zijn er uitschieters in frame detectie tijd.....
8:26 PM
Ik zal de bestaande timingmetrieken en captures koppelen aan de twee framingpaden. De standaard-responselezer hoort een exact gevormde FC03/04-response direct te herkennen; de generieke Shine-ingress gebruikt daarentegen de CRC-/gap-scan met een timeout van 10 seconden. Ik controleer welke pad en welke meetwaarde de gemelde uitschieters veroorzaken voordat ik een oorzaak concludeer.
Read files, ran commands
Ook updates van de RPi broker mogen voorbereid worden, maar ook dan moet ik kunnen ingrijpen en in de gaten houden hoe de growatt portal reageert. Die bug moeten we zeker ook fixen, maar gewoon geen writes of live update van de RPi als ik AFK ben. &#x20;
8:28 PM
Ran python3 - <<'PY' import json from pathlib import Path  def crc16(data):  crc=0xffff  for b in data:   crc^=b   for \_ in range(8):    crc=(crc>>1) ^ (0xa001 if crc&1 else 0)  return crc items=[json.loads(line) for line in Path('logs/sniff-live-d18b3db-20261002-rerun.jsonl').read_text(errors='ignore').splitlines()[12254:12256]] for d in items:  raw=bytes.fromhex(d.get('hex',''))  print({'event':d.get('event'),'length':len(raw),'unit':raw[0] if raw else None,'function':raw[1] if len(raw)>1 else None,'crc_valid':len(raw)>=4 and crc16(raw[:-2]) == int.from_bytes(raw[-2:],'little')}) PY rg -n 'def standard_response_spec|def find_standard_response' growatt_broker/broker.py
De brokerlogs bevestigen dat de metric echte uitschieters registreert: over 14.414 metingen is de mediaan 35,7 ms, p95 172,6 ms, p99 1,03 s en maximum 6,62 s. Dat zijn historische captures van 2 oktober. De teller meet CRC-scanwerk in `_first_crc_frame`, niet alleen de RTU-responstijd; bij de grootste uitschieter rapporteert dezelfde eventregel 382 ms fysieke latency en 6,73 s totale transactietijd. Ik onderzoek nu waarom die scan wordt herhaald/duur wordt en bouw een deterministische regressie rond het gevonden pad. Ik zal de broker alleen offline aanpassen en testen.
Read files, ran commands
Wellicht wat dat betreft als doel toevoegen er voor te zogen dat firmware updates via de broker Rpi in the middle veilig genoeg zijn om in de praktijk zonder zorgen te kunnen laten doen.
8:31 PM
Ik voeg dit toe als een expliciet veiligheidsdoel voor de broker: firmware-updateverkeer moet correct worden gedetecteerd, ongewijzigd en zonder concurrente RTU-interferentie worden afgehandeld, met timeout-/afbreekgedrag, traceerbaarheid en herstelprocedure aantoonbaar getest vóór praktijkgebruik. Dat is voorbereiding en testdekking; er komt geen live firmware-update of RPi-deployment bij deze werkzaamheden. Ik neem het op in de broker-roadmap als aparte reviewbare gate, zodat het niet wordt verward met de read-only virtual-invertertest.
Thinking

* * *

**ChatGPT:**

Alchemy pakt de uitbreiding behoorlijk goed op. Vooral de combinatie van de virtuele ZABA22, de passieve sniffer en het onderzoek naar de brokerlatency vind ik interessant. Dit begint een geïntegreerde testomgeving voor de Growatt-communicatie te worden, in plaats van alleen een alternatieve Shine-firmware.

Er vallen me drie dingen op.

### 1. De architectuur ontwikkelt zich in de goede richting

Alchemy heeft twee bestaande modellen geïdentificeerd: `InverterModel` voor Modbus-gedrag en `VppCompleteModel` voor de VPP/TOU-statusmachine.

**De keuze om deze via een adapter te combineren in plaats van opnieuw te implementeren is uitstekend.** Daarmee kun je de virtuele communicatieprocessor bovendien testen tegen zowel opgenomen als actuele invertergegevens.

De sniffer op `[redacted broker IP]:5700` blijkt bereikbaar. Dat is een belangrijke stap: Alchemy kan nu autonoom echte communicatie observeren zonder de inverter te beïnvloeden.

### 2. De brokerlatency verdient serieus onderzoek

Deze historische metingen zijn behoorlijk opvallend:

| Metriek | Framedetectietijd |
| --- | --- |
| Mediaan | 35,7 ms |
| P95 | 172,6 ms |
| P99 | 1,03 s |
| Maximum | 6,62 s |

Vooral het verschil tussen 382 ms gerapporteerde fysieke latency en 6,73 s totale transactietijd is interessant.

Als `_first_crc_frame` herhaaldelijk een steeds grotere buffer scant, kan de verwerkingstijd sterk oplopen. Maar dat is vooralsnog een hypothese, geen bewezen oorzaak.

Ik zou Alchemy hier specifiek laten controleren of het probleem voortkomt uit een kwadratische scan, een onnodige wachttijd bij incomplete frames, of verkeerde aannames over de verwachte framelengte.

Een robuustere framedetector moet bekende Modbus-responses direct op lengte kunnen herkennen en alleen voor onbekende of propriëtaire frames terugvallen op CRC- en timinggebaseerde detectie.

### 3. Firmware-updates via de Pi-broker zijn een apart vraagstuk

Je laatste toevoeging vind ik zinvol, maar hier zou ik één belangrijk onderscheid maken.

**Een firmware-update via een transparante RTU-broker is niet automatisch even veilig als een update via een rechtstreeks aangesloten originele Shine.**

Zelfs wanneer de broker geen bytes wijzigt, kan hij door buffering, vertragingen, reconnects of concurrerende transacties het updateprotocol beïnvloeden.

Om de broker geschikt te maken voor firmware-updates zou ik daarom een expliciete *exclusive passthrough mode* overwegen:

- Eén eigenaar van de fysieke inverterverbinding gedurende de hele updatesessie.
- Geen HA-polling, snifferinterferentie of andere Modbus-requests.
- Transparante doorgifte van alle updateframes, inclusief propriëtaire functiecodes.
- Geen automatische retries van programmeercommando's.
- Bewaking van timing, volgorde en volledige frame-integriteit.
- Bij verlies van de sessie niet automatisch opnieuw beginnen, maar een expliciete herstelprocedure volgen.

De sniffer kan daarbij passief blijven meelopen.

Maar zelfs na uitgebreide offline tests zou ik een update via de broker pas als betrouwbaar beschouwen nadat het transport met een geïsoleerd testapparaat of een veilige update-emulator is gevalideerd. De eerste praktijktest op een productie-inverter blijft een afzonderlijke beslissing.

### Wat ik vooral sterk vind aan Alchemy

Het corrigeert inmiddels ook zijn eigen testmethodiek. Het voorbeeld waarbij de onafhankelijke reviewer ontdekte dat de RTU-completionteller te vroeg werd verhoogd, is veelzeggend.

Dat is precies het soort fout waardoor een test ten onrechte kan slagen: er wordt gemeten dat een operatie begonnen is, terwijl de test moet bewijzen dat die werkelijk is voltooid.

Alchemy herkent die fout, past de implementatie aan en voert de tests opnieuw uit. Dat is een goed teken voor de kwaliteit van het autonome ontwikkelproces.

**Ik zou de koers voorlopig zo houden**, met drie duidelijk gescheiden werkstromen: virtuele inverter, brokerbetrouwbaarheid en firmware-update-transportveiligheid.

En ik zou één afspraak expliciet vastleggen: *live observatie mag autonoom; live writes, firmware-updates en wijzigingen aan de draaiende Pi-broker alleen wanneer jij aanwezig bent en vooraf expliciet toestemming hebt gegeven.*

Dat is ook precies hoe Alchemy je instructies nu lijkt te interpreteren.

* * *

**You:**

Nee, het is niet automatische even veilig, tenzij je exact begrijpt hoe het ding werkt. En open source firmware op zo'n ding zou helemaal niet zo gek zijn. Baas over eigen hardware. #hackaton

* * *

**ChatGPT:**

Precies. **Het doel moet niet zijn om een black box voorzichtig te omzeilen, maar om die black box zodanig te doorgronden dat hij geen black box meer is.** 😄

En dat is eigenlijk wat dit Growatt-project zo interessant maakt.

Je hebt inmiddels drie niveaus:

1. **ShineWiLan-X2:** de datalogger vervangen door eigen open-sourcefirmware.
2. **ZABA22:** de communicatieprocessor reconstrueren, inclusief Modbus, TOU, VPP en de interne toestand.
3. **ALBA18:** de DSP-communicatie begrijpen en uiteindelijk kunnen emuleren.

Wanneer je die componenten met elkaar kunt laten communiceren, ontstaat iets dat veel verder gaat dan een simpele Modbus-simulator: een reproduceerbare ontwikkel- en testomgeving voor een behoorlijk complex embedded systeem.

En je opmerking over firmware-updates is terecht. Als we het updateprotocol, de bootloader, de foutafhandeling en de transporttiming volledig begrijpen, kan een transparante broker in principe even betrouwbaar zijn als de originele verbinding. Dat moet je alleen wel aantonen, inclusief onderbroken updates en herstelgedrag.

Wat mij vooral aanspreekt, is de mogelijkheid om **de originele firmware als onderzoeksobject te gebruiken, maar de nieuwe implementatie volledig zelfstandig en open source te houden**.

Dan heb je niet alleen controle over je eigen hardware, maar geef je andere eigenaren ook de mogelijkheid om te begrijpen wat hun apparatuur doet.

Dat zou ook een mooie insteek zijn voor Savalle:

*Can an autonomous AI engineering system reverse-engineer a heterogeneous embedded platform and produce an independently testable, open-source replacement without access to the original source code?*

Met Growatt als concrete casestudy, en met echte protocolcaptures, Ghidra-reconstructies, virtuele processors en uiteindelijk hardwarevalidatie als bewijs.

**#hackathon — maar dan eentje waarbij het eindresultaat misschien daadwerkelijk jarenlang nuttig blijft.**

* * *

**You:**

Commit `6ac15bc` staat op `codex/broker-firmware-update-safety` en is naar `origin` gepusht; lokale en remote HEAD komen overeen.

* * *

**ChatGPT:**

Mooi, dan is de firmware-updateveiligheid van de RPi-broker nu als afzonderlijke ontwikkeltak vastgelegd:

- **Branch:** `codex/broker-firmware-update-safety`
- **Commit:** `6ac15bc`
- **Status:** gepusht; lokale en remote HEAD gelijk, volgens je rapportage.

Dat is een goede scheiding van de ShineWiLan-X2-vervangingsfirmware en de virtuele ZABA22.

Ik kon de commit niet terugvinden in `l4m4re/growatt-firmware`, dus de inhoud heb ik nog niet beoordeeld. Vermoedelijk staat deze branch in de afzonderlijke brokerrepository.

Voor de volgende beoordeling zou ik vooral willen vaststellen of Alchemy inmiddels **exclusief eigenaarschap van het RTU-kanaal, transparante framedoorgifte, timing en herstel na onderbreking** aantoonbaar heeft getest.

De code kan daarbij gewoon verder worden ontwikkeld en gepusht. Alleen het daadwerkelijk bijwerken van de live RPi-broker of uitvoeren van firmware-updates blijft een handeling waarvoor je aanwezig wilt zijn.

* * *

**You:**

Dit is in de rtu-broker repo

* * *

**ChatGPT:**

Gevonden! De commit staat inderdaad in [l4m4re/growatt-rtu-broker](https://github.com/l4m4re/growatt-rtu-broker/tree/codex/broker-firmware-update-safety?utm_source=chatgpt.com).

Ik heb commit [6ac15bc — Define practical firmware update safety gate](https://github.com/l4m4re/growatt-rtu-broker/commit/6ac15bce6d0d0ec194b3af98b31a300284b1434e?utm_source=chatgpt.com) gecontroleerd. De remote branch wijst ook naar exact die commit.

**Belangrijkste bevinding:** Alchemy heeft nog geen firmware-updatefunctionaliteit aangepast. De commit wijzigt uitsluitend `docs/ROADMAP.md` en legt de voorwaarden vast waaronder firmware-updates via de Pi-broker verantwoord getest kunnen worden.

De roadmap stelt onder andere deze eisen:

- Exclusief eigenaarschap van de inverterverbinding tijdens updates.
- Geen cache-antwoorden, automatisch herhaalde commando's of door elkaar lopende transacties.
- Byte-identieke frameoverdracht en offline replay met foutinjectie.
- Onderscheid tussen een Modbus-ACK en een werkelijk geslaagde firmware-update.
- Een aantoonbare herstelprocedure en onafhankelijke versiecontrole achteraf.

Daarnaast maakt Alchemy expliciet onderscheid tussen het updaten van de Raspberry Pi-broker zelf en het transporteren van inverterfirmware via de broker.

**Mijn beoordeling:** een goede voorbereidende commit, vooral vanwege de concrete acceptatiecriteria. Maar de interessante technische implementatie moet nog komen.

En dit sluit goed aan bij je uitgangspunt: niet zomaar aannemen dat een broker transparant genoeg is, maar op protocolniveau aantonen dat hij dat werkelijk is.
