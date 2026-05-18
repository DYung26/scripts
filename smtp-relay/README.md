# smtp-relay

A lightweight SMTP relay server that accepts mail from any SMTP client and forwards it through your real upstream SMTP credentials. Useful for centralizing email sending across apps without exposing your actual credentials to each one.

## How it works

The relay server listens on a TCP port and acts as a standard SMTP server. Clients connect to it with a set of relay credentials you define. When it receives a message, it forwards it through your real upstream SMTP account (e.g. Gmail, Mailgun, etc.).

## Setup

### 1. Configure environment variables

Copy `.env.example` to `.env` and fill in all values:

```env
# Upstream SMTP credentials — the actual account used to send emails out
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=465
EMAIL_USERNAME=you@gmail.com
EMAIL_PASSWORD=your_app_password

# Credentials the relay server expects from clients connecting to it
RELAY_USER=your_relay_username
RELAY_PASS=your_relay_password

# Port the relay server will listen on
RELAY_PORT=4001
```

- `EMAIL_*` — your real upstream SMTP credentials (e.g. a Gmail app password, Mailgun SMTP, etc.)
- `RELAY_USER` / `RELAY_PASS` — credentials you make up; clients must provide these to authenticate with the relay
- `RELAY_PORT` — the port this relay server listens on

### 2. Install dependencies and run

```bash
npm install
node email-relay.js
```

The server will log the port it's running on.

## Exposing the port

The relay needs its port reachable over TCP from wherever your clients are.

### Option A — Remote server (e.g. VPS)

Open an inbound TCP rule for the relay port. In most cloud providers (AWS, DigitalOcean, etc.) this means adding a custom inbound rule:

- **Protocol:** TCP
- **Port:** your `RELAY_PORT`
- **Source:** `0.0.0.0/0` (or restrict to known IPs)

Then point a subdomain (e.g. `relay.yourdomain.com`) at the server's IP with an A record.

### Option B — Local machine via ngrok

If you're running the relay locally, use ngrok to expose it as a TCP tunnel:

```bash
ngrok tcp 4001
```

ngrok will give you a forwarding address like `tcp://6.tcp.ngrok.io:12850`. Your clients use `6.tcp.ngrok.io` as the host and `12850` as the port.

## Connecting a client

From the client's perspective, this is just a standard SMTP server. Replace your usual SMTP credentials with the relay's address and your relay credentials.

### Nodemailer (Node.js)

```js
const transporter = nodemailer.createTransport({
  host: "relay.yourdomain.com", // or ngrok host
  port: 4001,                   // or ngrok port
  secure: false,
  auth: {
    user: "your_relay_username",
    pass: "your_relay_password",
  },
});
```

### Python (smtplib)

```python
import smtplib
from email.mime.text import MIMEText

msg = MIMEText("Hello!")
msg["Subject"] = "Test"
msg["From"] = "you@example.com"
msg["To"] = "recipient@example.com"

with smtplib.SMTP("relay.yourdomain.com", 4001) as server:
    server.login("your_relay_username", "your_relay_password")
    server.send_message(msg)
```

Any SMTP client works — just swap in the relay host, port, and relay credentials instead of your real upstream ones.
