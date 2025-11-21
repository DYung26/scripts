import { SMTPServer } from "smtp-server";
import nodemailer from "nodemailer";
import * as dotenv from 'dotenv';

dotenv.config();

const host = process.env.EMAIL_HOST;
const port = process.env.EMAIL_PORT;
const user = process.env.EMAIL_USERNAME;
const pass = process.env.EMAIL_PASSWORD;
console.log("Host:", host, port, user, pass ? "****" : "nope");

const gmailTransport = nodemailer.createTransport({
  host,
  port,
  secure: false,
  auth: {
    user,
    pass,
  },
  tls: { rejectUnauthorized: false },
});

const server = new SMTPServer({
  logger: true,
  // authOptional: true,
  onAuth(auth, session, callback) {
    if (auth.username === process.env.RELAY_USER && auth.password === process.env.RELAY_PASS) {
      callback(null, { user: auth.username });
    } else {
      return callback(new Error("Invalid username or password"));
    }
  },
  onData(stream, session, callback) {
    let email = "";
    stream.on("data", chunk => (email += chunk.toString()));
    stream.on("end", async () => {
      try {
        await gmailTransport.sendMail({
          envelope: {
            from: session.envelope?.from || user,
            to: session.envelope?.to || session.envelope?.rcptTo?.map(r => r.address),
          },
          raw: email,
        });
        console.log("Email relayed successfully");
      } catch (err) {
        console.error("Relay failed:", err);
      }
      callback();
    });
  },
});

server.listen(4000, "0.0.0.0", () => {
  console.log("Relay server running on port 4000");
});

