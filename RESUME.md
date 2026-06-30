# RESUME — appdata-disk död + stack återuppbyggd (2026-06-29)

## VAD SOM HÄNDE
USB-nvme `appdata` (458G, LUKS, `/dev/sda`) dog vid omstart: DID_ERROR → USB disconnect → ext4 emergency_ro → EIO. Hårdvarufel. TR-002/`.data` (sdb, 21,8T) är separat och frisk. Disken fysiskt urdragen.

## SYSTEM-OMKONFIG (klart)
- docker `data-root` → default `/var/lib/docker` (systemdisk /). daemon.json.bak-20260629.
- /etc/fstab + /etc/crypttab: appdata bortkommenterad (*.bak-20260629).
- dangling dm-0 kvar tills nästa reboot (ofarlig).

## RÄDDNING (lyckad)
Under döda monteringspunkten låg en hel kopia (20 maj) på friska /home, dold av mounten:
.env, .secrets (cf_tunnel_token!), radarr/sonarr/prowlarr+DB, qbittorrent, recyclarr, maintainerr, dozzle, postgres.
- arr-DB:er uppgraderade 20 maj → 14 jun via OneDrive-zippar (20 maj = *.db.may20-bak).
- Lokal backup-kopia av OneDrive: /home/berduf/restore-20260629/

## STACK (återuppbyggd, körs)
Compose: /home/berduf/.appdata/.config/docker-compose.yml (bas = OneDrive 16 jun). .env + secrets på plats.
Borttaget: qui (25 jun), portainer (användarval). 12 tjänster:
cloudflared, dozzle, maintainerr, plex, postgres, prowlarr, qbittorrent, radarr, recyclarr, scraper, sonarr, watchtower.
${DOCKER}=/home/berduf/.appdata (nu på frisk /home, samma sökväg).

## KVAR ATT GÖRA
1. Plex: klamras om / verifiera bibliotek (plex/Library var förlorad; PLEX_CLAIM i .env).
2. maintainerr: state förlorat (exkl. backup) → konfigurera om vid behov.
3. scraper: live-DB borta → börja om scrape, sedan enrich → CF sync-deploy (gammal plan från noll).
   - CF deploy-token CLOUDFLARE_API_TOKEN död → testa politiker-token (Workers Scripts:Edit) / rotera.
4. Backup-script (docker-backup.sh): OneDrive-token nu återansluten 2026-06-29. DOCKER_DATA pekar rätt (/home/berduf/.appdata). LÄGG TILL larm när backup misslyckas (tyst i 11 dagar 19→29 jun).
5. Verifiera att daglig 02:00-backup går igenom nu.

## SCRAPER ÅTERSTÄLLD (2026-06-30) — var INTE migrerbar
Misstag rättat: scrapern (Playwright) är hård lokal beroende av CF-sync-Workern (README rad 81). Återställde scraper+postgres i compose + scraper-api.denied.se i tunneln (ingress+DNS verifierat, /health -> 200 genom tunneln). webui-routen scraper.denied.se förblir borttagen (bara mänskligt UI, ingen Worker rör den). postgres TOM -> re-scrape krävs för data.
Arkitektur: server kör scraper.py (crawl) + enrich.py (backlog category/source_text) + api:8765 + webui:3000 + alerts + postgres. CF kör product-describer app/processor (filuppladdning) + sync (cron 5 min pollar scraper-api). Enda krävda route: scraper-api.denied.se.
NÄSTA: kostnadsanalys CF (Browser Rendering + D1 + Workers) vs egen server.

## ENRICH-FIX (2026-06-30) — B.2 implementerad
Katalog återställd från CSV-export: /home/berduf/.claude/uploads/.../34fcfe05-products_20260624.csv (32 500 produkter) importerad till postgres (UPSERT på url, site_config_id=3). Postgres nu ~32 634 produkter (var 1314).
Kvalitetskoll avslöjade: enrich tog webhallens og:description-BOILERPLATE, inte riktig text — webhallen injicerar Product-JSON-LD client-side, enrich extraherade för tidigt.
FIX i blixten85/scraper PR #219 (branch claude/enrich-detail-selector):
- enrich_one väntar in Product-JSON-LD (RENDER_WAIT_MS=12s) före extraktion.
- scraper_config.detail_selector (ny kolumn) — per-sajt CSS, provas först (för sajter utan JSON-LD, t.ex. Inet som saknar strukturerad data helt).
- Verifierat: vanliga webhallen-produkter ger nu 158–631 tecken riktig text. Fyndvara (~8%, 2607 st) saknar JSON-LD → boilerplate (acceptabelt).
- DB-kolumn detail_selector redan tillagd manuellt (idempotent, = schema-migreringen).

#219 MERGAD + DEPLOYAD (2026-06-30). OBS: auto-merge via github-actions-bot triggar INTE CI på main (GITHUB_TOKEN-loopskydd) -> körde `gh workflow run ci.yml --ref main` manuellt för att bygga/pusha :latest. Scraper pull+recreate klar, RENDER_WAIT_MS verifierad i imagen.

BACKLOG KÖR NU (startad 2026-06-30 ~06:57):
- `docker exec -d scraper sh -c "python -m scraper.enrich --site Webhallen --concurrency 3 > /logs/enrich_backlog_20260630.log 2>&1"`
- 31546 produkter, ~12-14h. Resumabel (utan --refresh; webhallen source_text nollställdes först).
- ÖVERVAKA: docker exec scraper tail -f /logs/enrich_backlog_20260630.log  ELLER  SELECT count(*) FROM products WHERE site_config_id=3 AND source_text IS NOT NULL;
- ÅTERUPPTA om avbruten: samma docker exec-kommando (plockar kvarvarande NULL).

KVAR:
1. Inet (1055 st): sätt scraper_config.detail_selector via DB när rätt selektor hittats (Inet saknar JSON-LD; ingen ren selektor hittad än).
2. CF-sync (cron 5 min) genererar description grundat på source_text. OBS: sync väntar inte på source_text -> kan beskriva produkter innan deras source_text fyllts. Om viktigt: pausa sync tills backlog klar, eller filtrera sync på source_text IS NOT NULL.
3. Fyndvara (~2607) får boilerplate (saknar Product-JSON-LD) — ev. ärva beskrivning från moderprodukt senare.

## ENHETLIG ARKITEKTUR (2026-06-30) — pågår
DESIGN.md i product-describer-cloudflare. CF=hjärna+minne (D1), server=statslös Playwright-fetcher (pull). Noll kostnad (render_jobs-tabell, inte Queues; Playwright lokalt).
- Fas 1 KLAR (#16 mergad + DEPLOYAD): D1 katalog-schema applicerat på live product_describer-DB; engine-Worker på **engine.denied.se** (workers.dev blockerat -> custom domain, #17). Secret INGEST_API_KEY satt + sparad i /home/berduf/.appdata/.config/.env.
- Fas 2 KLAR (#220): fetcher/fetcher.py (scraper-repot). VERIFIERAD end-to-end mot live D1 (lease->render->result, titel/pris/source_text/prishistorik). Kör via scraper-imagen: docker run -e ENGINE_URL=https://engine.denied.se -e INGEST_API_KEY=... python /app/fetcher/fetcher.py
- Engine-endpoints: POST /jobs/lease, POST /jobs/:id/result, POST /ingest, GET /health (X-API-Key).
- Deploy engine: cd product-describer-cloudflare/engine; CLOUDFLARE_API_TOKEN=<politiker-deploy ur file-history v11>; npx wrangler deploy. D1: npx wrangler d1 execute product_describer --remote ...
KVAR:
- Fas 3 migrering: när lokal-postgres-backloggen är klar -> exportera products/source_text/price_history -> D1 (POST /ingest eller wrangler d1). Test-rader (Deltaco id1) finns redan i D1.
- Fas 4 KLAR (#18, DEPLOYAD): EN cron (*/5) i engine — reclaimLeases / scheduleDetailJobs / describeMissing. describe hoppas över tills GEMINI_API_KEY sätts (gratis): cd product-describer-cloudflare/engine && npx wrangler secret put GEMINI_API_KEY. GEMINI_API_KEY satt 2026-06-30 (free tier, projekt Product-describer). Cron körs live men ofarligt (få produkter i D1 tills migrering).
- list-jobb (discovery): lease måste returnera list-selektorer; fetchern utökas.
- Fas 5 alerts+UI, Fas 6 riv lokal postgres/scraper-API.

## ÅTERSTÄLLNING om strul
Lägg tillbaka *.bak-20260629 (daemon.json/fstab/crypttab) + docker-compose.yml.may18-bak och boota.
