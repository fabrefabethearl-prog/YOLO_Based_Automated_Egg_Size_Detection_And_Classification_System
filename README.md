# EggScan

EggScan is a local lab tool for a capstone on chicken egg size detection. A camera frame goes through YOLO, the box is converted to millimetres, and a size class is saved. Size is estimated from the image. The size class comes from the measured length and area.

The dashboard is a plain HTML page served by FastAPI. There is no demo data until you ask for it.

## Setup

Python 3.10 or newer. From this folder:

```text
python -m venv .venv
```

Windows:

```text
.venv\Scripts\activate
pip install -r requirements.txt
```

macOS or Linux:

```text
source .venv/bin/activate
pip install -r requirements.txt
```

Before the first run, copy `.env.example` to `.env` and configure `SECRET_KEY`, `ADMIN_EMAIL`, and Gmail SMTP settings. Authentication is administrator-only; there is no public registration or default password account. Set `SMTP_EMAIL` to the Gmail account that sends the codes and `SMTP_APP_PASSWORD` to its Google App Password (Google requires 2-Step Verification for App Passwords). Never use your normal Gmail password. Keep `.env` private and never commit it.

Generate a random secret in PowerShell:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set the permitted administrator address in `ADMIN_EMAIL`; it should match the administrator account already provisioned in the SQLite database. Generate a Google App Password for the sending account and enter it in `SMTP_APP_PASSWORD`. `SMTP_PORT=587` uses STARTTLS; port `465` uses SSL. The sending account may be different from `ADMIN_EMAIL`.

After setting `ADMIN_EMAIL`, start EggScan once to create its administrator row, stop the server, then set the login password with a hidden prompt. Choose a new password that has not been shared in chat:

```powershell
.\.venv\Scripts\python.exe -m backend.set_admin_password
```

Set `ADMIN_EMAIL` and `SMTP_EMAIL` to `zempyoo@gmail.com` if that is both your sign-in account and the Gmail sender. In your Google Account, enable 2-Step Verification and create an App Password; put that value in `SMTP_APP_PASSWORD` in `.env`. Never use or share your regular Gmail password. Restart EggScan after editing `.env`.

## Deploy to Render

The repository includes a Render Blueprint in `render.yaml`. It runs the app on Render's assigned `$PORT`, enables HTTPS-aware proxy headers, and mounts a persistent disk at `/var/data` for the SQLite database, scans, and datasets. The Starter web service and persistent disk are paid Render resources; SQLite requires a single app instance.

1. Push this repository to GitHub and create a **Blueprint** in Render using that repository and `render.yaml`. Keep `EGGSCAN_LOCAL_DEMO_LOGIN` set to `false`; public deployments must not use password-only demo sign-in.
2. After the service is created, open its **Environment** settings and set:
   - `ADMIN_EMAIL` to an inbox you can access. It receives administrator sign-in codes and must match the email you enter at login.
   - `SMTP_EMAIL` to the Gmail account used to send codes, and `SMTP_APP_PASSWORD` to that account's Google App Password. Never use your regular Gmail password.
   - `EGGSCAN_PHONE_BASE_URL` to the service's exact public HTTPS origin, for example `https://eggscan.onrender.com`, with no path or trailing slash.
3. Save the settings and wait for the service to redeploy. On its **Shell** tab, run `python -m backend.set_admin_password` and enter a unique password of at least 14 characters at the hidden prompt. This provisions the admin password; it is not stored in `.env` or Render environment variables.
4. Open the Render service URL, sign in with `ADMIN_EMAIL` and the password you just set, and complete email verification. To use the phone camera, open **Live Scan**, choose **Phone camera**, scan the QR code, and approve camera access on the phone.

The QR pairing link expires after five minutes and can only be used once. Render provides HTTPS for the dashboard and signaling. WebRTC video may need a TURN relay to connect phones across restrictive networks; configure `EGGSCAN_TURN_URLS` and the server-side `EGGSCAN_TURN_SHARED_SECRET` in Render's Environment settings if direct STUN connectivity is not sufficient. Never expose the TURN shared secret in frontend code. A trained `size/best.pt` model is not included in the Blueprint; without it, EggScan uses its built-in fallback detector.

### Render troubleshooting

If Render logs show `npm run start` or a missing `package.json`, the service is configured as a Node service instead of using this Python Blueprint. Do not add a `package.json` to this Python project. In Render, sync/deploy the Blueprint from the repository root so the `eggscan` service uses the Python runtime and the commands in `render.yaml`. If configuring a web service manually, set **Root Directory** to the repository root, **Runtime** to Python, **Build Command** to `pip install -r requirements.txt`, and **Start Command** to `python -m uvicorn backend.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'`. Save and redeploy; the logs should show Uvicorn listening on `0.0.0.0` and Render's assigned port.

## Run

```text
uvicorn backend.main:app --reload
```

Open http://localhost:8000

### Phone camera on Live Scan

The phone page uses HTTPS for camera permission, WSS for signaling, and WebRTC for video. The private-LAN setup below works only when the devices can route to each other. For a phone on mobile data or another network, the app needs a public HTTPS origin and WebRTC ICE servers. STUN attempts a direct path; TURN relays media when NATs or firewalls prevent a direct path. The app does not record or store the phone video stream.

#### Same Wi-Fi only

Use this option when both devices are on the same non-guest Wi-Fi and the router allows devices to communicate. The phone link expires after five minutes and can be used once.

1. Install [mkcert](https://github.com/FiloSottile/mkcert) on the computer and run `mkcert -install` from an administrator PowerShell if Windows requests it.
2. Find the computer's Wi-Fi IPv4 address with `ipconfig` and reserve it in the router if possible. For example, use `192.168.1.20` below.
3. Create a certificate containing that address:

   ```powershell
   mkcert -cert-file eggscan.pem -key-file eggscan-key.pem localhost 127.0.0.1 ::1 192.168.1.20
   ```

4. Copy only the mkcert root certificate (`mkcert -CAROOT` shows its folder; the file is `rootCA.pem`) to the phone and install/trust it as a certificate authority. Never copy `rootCA-key.pem` to the phone.
5. Set `EGGSCAN_PHONE_BASE_URL=https://192.168.1.20:8000` in `.env`, using the computer's actual private Wi-Fi address and port. Local pairing accepts a private LAN IPv4 URL; localhost, public IP addresses, paths, and non-HTTPS URLs are rejected.
6. Start EggScan bound to the LAN interface with the certificate:

   ```powershell
   .\.venv\Scripts\uvicorn.exe backend.main:app --host 0.0.0.0 --port 8000 --ssl-certfile eggscan.pem --ssl-keyfile eggscan-key.pem
   ```

7. Open `https://localhost:8000` on the computer and sign in, then choose **Phone camera** and click **Start scan**. Scan the QR code with the phone, open the link in Chrome, tap **Start Sharing**, and allow camera access. The QR link uses the private Wi-Fi address from `EGGSCAN_PHONE_BASE_URL`. Allow the server port through Windows Firewall for the active network if prompted. Guest Wi-Fi or router client isolation can block device-to-device traffic.

#### Cross-network testing with a protected Cloudflare Quick Tunnel

This keeps EggScan running on the computer while giving it a temporary public HTTPS hostname. The tunnel carries the dashboard, API, and WebSocket signaling; WebRTC video travels directly between the devices when possible, or through the separately configured TURN relay. Quick Tunnels are for temporary testing, their hostname changes when restarted, and the current Cloudflare CLI supports restricting access to an allowed email address. See the [Quick Tunnel guide](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

1. Install `cloudflared` on Windows and choose an email address you control for the access restriction.
2. In a terminal, start the tunnel. It will print a temporary `https://....trycloudflare.com` URL. Keep this terminal open:

   ```powershell
   cloudflared tunnel --url http://127.0.0.1:8001 --allowed-mail you@example.com
   ```

   Replace `you@example.com` with the email address that should be allowed to open EggScan. Do not start an unrestricted public tunnel.

3. In `.env`, set `EGGSCAN_PHONE_BASE_URL` to the exact HTTPS URL printed by `cloudflared`, and set `SECRET_KEY` to a newly generated random secret. Keep `.env` private. For example:

   Generate a secret in PowerShell:

   ```powershell
   .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   ```text
   EGGSCAN_PHONE_BASE_URL=https://your-random-name.trycloudflare.com
   SECRET_KEY=<a-long-random-secret>
   ```

   Do not include a path or trailing slash in the base URL. Keep the Cloudflare email restriction enabled and do not share the tunnel URL publicly.

4. Configure STUN and TURN in `.env`. STUN is used by default for direct connection attempts. For reliable cross-network use, obtain a TURN service that supports coturn's shared-secret REST credentials. Add its TURN URLs and server-side shared secret:

   ```text
   EGGSCAN_STUN_URLS=stun:stun.l.google.com:19302
   EGGSCAN_TURN_URLS=turn:turn.example.net:3478?transport=udp,turns:turn.example.net:5349?transport=tcp
   EGGSCAN_TURN_SHARED_SECRET=<the-server-side-shared-secret>
   EGGSCAN_TURN_CREDENTIAL_TTL_SECONDS=3600
   ```

   Replace the example TURN host and secret with values from the TURN provider. Never put the shared secret in frontend code or send it to the phone. The app creates temporary TURN usernames and passwords for the paired session. Without TURN, direct WebRTC may still work, but some mobile networks and NATs will block it.

5. In a second terminal, run the app on loopback so it is reachable by the tunnel but not directly exposed on the LAN. Uvicorn trusts forwarded HTTPS headers only from the local tunnel process:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8001 --proxy-headers --forwarded-allow-ips=127.0.0.1
   ```

   Keep both terminals open. The app must be restarted after changing `.env`.

6. On the computer, open the tunnel's HTTPS URL, complete the allowed-email prompt, and sign in to EggScan. Select **Phone camera** and click **Start scan**. On Android, scan the new QR code, open its `/phone-camera#...` link in Chrome, complete the allowed-email prompt if asked, tap **Start Sharing**, and allow camera access.

7. Test on mobile data with Wi-Fi turned off. The phone and computer must both use the same public tunnel URL for the dashboard and pairing page. The QR code expires after five minutes and is single-use.

Cloudflare Quick Tunnels are temporary test links, not a stable deployment. For regular use, set up a named tunnel with a domain, configure a Cloudflare Access allowlist for the app hostname, and keep the EggScan origin bound to loopback. Named tunnels require a Cloudflare account and a domain on Cloudflare. Domain registration and TURN relay/bandwidth pricing depend on the providers you choose; this repository does not create accounts, provision a hostname, or provision a TURN server. See [Cloudflare Tunnel setup](https://developers.cloudflare.com/tunnel/get-started/) and [coturn's temporary credential documentation](https://github.com/coturn/coturn/blob/master/man/man1/turnserver.1).

#### ICE configuration

`EGGSCAN_STUN_URLS` accepts comma-separated `stun:` or `stuns:` URLs. `EGGSCAN_TURN_URLS` accepts comma-separated `turn:` or `turns:` URLs. Set `EGGSCAN_TURN_SHARED_SECRET` only when using a TURN server configured for coturn REST/shared-secret authentication. Keep it server-side and at least 32 characters long. `EGGSCAN_TURN_CREDENTIAL_TTL_SECONDS` controls the temporary credential lifetime (300 to 86400 seconds). The app returns only temporary credentials to the paired browsers, never the shared secret. Use ICE services you own or are authorized to use.

The STUN/TURN list is provided to both peers when the QR pairing is created/validated. The phone checks the pairing before it asks for camera permission. Android Chrome still requires a tap on **Start Sharing**; a QR code cannot grant camera permission or start capture silently.

Sign in with the administrator email and password, then enter the one-time code sent by Gmail. Codes expire after 5 minutes, allow up to 5 guesses, and can be resent once per minute. The code and browser challenge are stored as hashes in SQLite; the administrator session is created only after successful verification and is set in a random HttpOnly cookie, with only its hash stored in SQLite. Set `EGGSCAN_COOKIE_SECURE=true` when HTTPS is terminated by a reverse proxy. Local plain-HTTP development defaults to a non-Secure cookie.

## What you should see

If `size/best.pt` is missing, the top bar says **Mock mode**. Scans still run: OpenCV looks for one egg-shaped object so you can demo the pages. A real model replaces that path and the badge switches to **YOLO** after a restart.

Overview, records, model metrics, and logs start empty. Accuracy shows "No data yet" until a scan has an actual size class.

For a presentation with sample charts:

```text
python -m backend.seed_demo
```

`--force` deletes existing scans, benchmark trials, and logs first.

## Calibrate

1. Keep the camera still. Measure a known length in the image; the saved **pixels per mm** calibration turns the box into millimetres. Example: 40 mm across 200 pixels is 5.
2. The Settings page is no longer part of the dashboard. The saved classification mode is still used: **dimensions** assigns size from area bands (length × width), while **model** uses detector class names that match the size classes.
3. After a scan, the size class comes from the measured length and area. You can still record that measured class on the scan when you want it in the accuracy table.
4. **Fit size bands from verified eggs** needs at least 10 scans with an actual class, ideally five or more in each class. It shows a before/after accuracy table and saves only after you confirm.

Default area bands, in square millimetres, are a starting point. Fit them from verified eggs before you treat them as final:

| Class | Minimum area (mm²) |
| --- | --- |
| Small | 2250 |
| Medium | 2420 |
| Large | 2560 |
| Extra Large | 2730 |
| Jumbo | 2900 |

Saving a new table recomputes the predicted class on every stored scan. Minimums must strictly increase from top to bottom. The actual size class you chose is left as it is.

## Annotate dataset images

In the Dataset section, upload an image folder into its matching size class and choose **Annotate**. Opening an unannotated image automatically detects egg contours against a dark background using OpenCV, fits an ellipse for geometric measurement, and displays the detected contour. Length and width use the saved camera scale; verify the values against a ruler and draw manually when detection needs correction. Existing annotations load when their image is opened, and **Restore saved annotation** discards unsaved edits. Review the detected contours and filename/reference label before saving. A filename containing exactly one `Small`, `Medium`, `Large`, or `Jumbo` token supplies a reference label; ambiguous names require a manual choice. Reference labels are separate from the dataset-folder/training class and are not verified physical sizes.

The dataset and model retain their existing five classes, including `Extra Large`. The reference-label workflow does not change that mapping or infer a model prediction.

## Train or swap the model

See `ml/README.md` for local dataset annotation and the 70/20/10 split.

```text
python ml/train.py
python ml/evaluate.py
```

Training copies the best checkpoint to `size/best.pt`. Restart uvicorn. The next scan uses YOLO and is not marked mock.

You can also drop an existing `best.pt` into `size/` yourself. It must be an Ultralytics detection model. A single class named `egg` uses the area bands. A model whose class names are Small, Medium, Large, Extra Large and Jumbo is used as the predicted size class when the saved classification mode is **model**.

## Tests

```text
pytest
```

## Troubleshooting

**Cannot reach the EggScan server.** EggScan serves the login page and `/api/auth/login` from the same FastAPI origin. From the project folder, start the backend in PowerShell and open the page at `http://127.0.0.1:8000` (do not open `frontend/index.html` directly or use a separate static-file server):

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Keep that terminal open. If port 8000 is already in use, change both `--port 8000` in the command and the browser URL to `8001`. Sign in with the administrator email and password.

**Mock mode stays on.** `size/best.pt` is missing, smaller than a real checkpoint, or Ultralytics could not load it. Check the Activity logs for an error, then restart.

**No egg detected.** The shell and background are too similar, the egg is cut off, or confidence is below the saved threshold. Improve the image conditions before changing confidence. Mock mode needs a clear egg-shaped blob.

**Sizes look wrong.** `pixels_per_mm` is the usual cause. Re-measure at the same camera height, then fit the area bands from verified eggs.

**Accuracy is "No data yet".** That number uses only scans with an actual size class. Unverified eggs still count toward Total scanned.

**Camera will not start.** Allow the browser to use the camera on localhost, or use Upload image. Camera index `0` is the first device.

Administrator sessions are revocable and SQLite stores only their token hashes.

**Port already in use.** Stop the other process or run `uvicorn backend.main:app --reload --port 8001`.

**Upload rejected.** JPEG, PNG, or WebP only, up to 10 MB. Dataset labels must be YOLO lines: `class x y w h`.

**Training says there are no images.** Put files in `data/dataset/images/train` and `val`, with matching files under `labels/`.

## You still need to provide

- A labelled egg dataset (images + YOLO txt labels) if you want a real detector.
- `size/best.pt`, either from `python ml/train.py` or copied in from your own training run.
- A `pixels_per_mm` measurement at the camera height you will use.
- An actual size class on the eggs you want in the accuracy and confusion matrix. That class is the measured size.
- A check that the area bands match the eggs you measured, using Fit size bands once you have at least 10 verified scans.
