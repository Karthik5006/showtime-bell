# Showtime Bell

Movie ticket alerts for your favorite cinemas. Showtime Bell currently watches ALLU Cinemas in Kokapet and Prasads Multiplex in Hyderabad, including PCX. When a booking opens on BookMyShow or District, the watcher sends the showtime and booking link to Telegram.

## GitHub Actions notifications

**Actions → Showtime alerts** checks on a five-minute cron schedule and when watcher code changes on `main`. GitHub may delay or drop scheduled jobs; this is not a precise five-minute timer. The workflow runs a full Chromium session under Xvfb. It performs one scan and exits, instead of running an endless worker in Actions.

1. In **Settings → Secrets and variables → Actions → Secrets**, add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. See “Connect your phone” below for obtaining them. Never put these in source code or issues.
2. Open **Actions → Showtime alerts → Run workflow** on `main`. Leave the delivery-test checkbox enabled. Confirm the clearly labeled test arrives in Telegram.
3. Read the **Coverage summary**. A successful initial scan records existing shows silently; later newly enabled shows trigger messages. Three consecutive incomplete scans send a coverage warning. A delivery test confirms Telegram only, not booking-site access.

Without the two secrets, the workflow runs read-only inspection and reports that notifications are not configured. The workflow saves the SQLite show inventory to a separate `showtime-state` branch, including after incomplete scans. That database contains public show listings and delivery/health status, never credentials. Do not delete the branch unless you intend to reset the baseline. State API failures stop the scan rather than silently losing history. Concurrent runs share one writer. A crash after delivery but before saving state can still produce a repeated alert.

### If a booking site returns 403

A 403 is denied access, not an empty cinema. The earlier GitHub live trial returned 403 for both BookMyShow and District. Full browser mode may help browser compatibility but does not guarantee acceptance of GitHub's data-center connection. The watcher stops additional requests to a denied platform for that scan, retains prior inventory, and reports incomplete coverage.

If the full-browser trial is still blocked, provide a connection on which you are authorized to access the public listings:

- Add optional Actions secrets `BROWSER_PROXY_SERVER` (for example `http://proxy-host:port`), `BROWSER_PROXY_USERNAME`, and `BROWSER_PROXY_PASSWORD` for a proxy you control or are authorized to use. No proxy is bundled or purchased.
- Alternatively register a dedicated Linux self-hosted GitHub runner with a custom label, then set repository variable `WATCH_RUNNER` to that label. It needs Python, sudo/apt, and a working connection to the booking sites. The same scheduled workflow will use it. Do not enable untrusted pull-request jobs on that runner.

The code does not solve CAPTCHAs or infer availability from challenge pages. Use the coverage summary to verify the chosen connection actually works. GitHub can disable schedules on inactive public repositories after 60 days; check Actions if alerts stop.

## Offline and live tests

**Test Showtime Bell** runs the offline trials on pushes to `main` and pull requests. Its optional **Check live booking pages** input retains the original Docker-based headless inspection for comparison. **Showtime alerts** uses the new full-browser path and runs the offline regression tests before scanning.

## Run the alert service

For ongoing five-minute polling, deploy the Docker worker on an always-on host. A small Always Free Oracle A1 VM is one option if there is capacity and you are eligible. Use an Always Free image, compute and storage in your home region, install Docker with Compose, clone this repo onto the VM, then follow the steps below. Keep the Telegram token and chat ID in the VM's private `.env`. The worker stores show history in its persistent volume.

Run this Python worker on an always-on Linux machine or VPS with Docker Compose. It checks BookMyShow and District and sends Telegram messages directly to your phone. It uses the host's internet connection. The default delay between completed scans is five minutes. The host must be awake for checks to run.

## Try it before entering any credentials

Clone this repository or extract its source ZIP. Open the project folder and run the commands below.

```sh
cp .env.example .env
docker compose build
docker compose run --rm --entrypoint python watcher -m unittest -v
docker compose run --rm watcher inspect
```

The offline trial compares a film with an enabled 1:15 PM show and disabled 1:20 PM show against a second snapshot where the 1:20 PM button becomes enabled. It checks that exactly one alert is queued, retries failed delivery after restart, and checks that matching District and BookMyShow shows share an alert.

The live inspection prints the venue, date, enabled-show count and individual records. Check a couple of the printed times against the booking app on your phone. A District directory match of “neither target cinema” means neither venue was identified in the public directory. An “unknown” or a nonzero exit signals incomplete coverage. Run this on the same host you intend to deploy to. Website access can differ between a home connection and a VPS.

## Connect your phone

1. Open Telegram, message **@BotFather**, use `/newbot`, and put the token in `TELEGRAM_BOT_TOKEN` in your local `.env`.
2. Open the new bot in Telegram and send `/start`. Run `docker compose run --rm watcher chat-id`. Use a new bot that only you have messaged so you can identify your private chat ID. Set `TELEGRAM_CHAT_ID` in `.env`.
3. Run `docker compose run --rm watcher test-alert`. Confirm the message arrives on your phone. This sends a clearly labeled delivery test.
4. Run `docker compose up -d` to start the background worker. Use `docker compose logs --tail=60 -f` to see the first scan. A successful initial scan records existing shows silently. Closing the terminal does not stop the worker.

Keep `.env` private. On a shared Linux host use `chmod 600 .env`. To change the interval, edit `POLL_SECONDS`, then run `docker compose up -d` again. To stop it, run `docker compose down`. Keep the named volume when stopping or upgrading. Deleting the volume deletes the booking history. After a reboot Docker must start automatically. Use one worker with one local volume.

## What triggers an alert

- **BookMyShow** checks ALUC beside Allu Studios, Kokapet, and PRHN on NTR Marg, including PCX. It follows the enabled dates in the venue calendar, reads the rendered schedule, and checks each show's own Book button. Screen labels appear in alerts when the listing provides them.
- **District** searches the public Hyderabad cinema directory for the two names, then validates the address on the venue page and follows its dated listings. A show needs an enabled time control containing a seat-layout link. The watcher never opens that link. A venue present solely in the mobile app or outside the public directory can be missed. If a venue appears after a prior directory check, its verified shows can trigger alerts.
- Dates that first open after the baseline and newly enabled shows are eligible. Every language and film is included. Common Dune and Marvel names get a star as a display hint. Seat counts and prices are omitted.
- SQLite retains the last successful date inventory and queued messages across restarts. Matching venue, film, time, language and normalized format are combined across platforms. Different spellings or missing format labels can prevent a match. A Telegram success followed by an immediate host crash can cause a repeated message on restart.
- After three consecutive incomplete scans of a platform and venue, the worker sends one coverage alert. Failed date reads retain the prior observation. A sleeping host or network failure that also blocks Telegram cannot send an outage alert. The watcher relies on the public website controls, so site changes or blocked access can affect coverage. There is no guaranteed opening-to-alert time.

## Files

`sources.py` reads the listings. `watcher.py` handles polling, durable state and delivery. `test_watch.py` contains the offline trials. `Dockerfile` and `compose.yaml` deploy the worker. The ChatGPT hourly watch is a separate service.
