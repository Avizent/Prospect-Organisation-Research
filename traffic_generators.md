# ANS Product Knowledge — Traffic Generators

**Vendor:** Apposite Technologies (ANS is Authorised Distributor and Official Reseller)
**Product suite:** Netropy TrafficGen — AppStorm, TrafficEngine, SessionStrike, AppPlayback, CyberAttack

---

## What traffic generators do

Traffic generators create realistic, high-volume network traffic in a controlled lab environment to stress-test network devices and infrastructure before deployment. They complement network emulators: where emulators simulate *conditions* (latency, loss, bandwidth), traffic generators simulate *load* (realistic application flows, attack traffic, millions of endpoints).

Primary use cases: performance benchmarking, capacity planning, security validation, QoS testing, load testing.

---

## Product suite overview

### AppStorm — Application-Aware Device Testing

Tests firewalls, SD-WAN gateways, and other stateful/application-aware devices at scale.

Key capabilities:
- Validates performance of application-aware devices using an extensive library of pre-defined application flows
- Emulates clients, servers, and real network traffic with varied application mixes and protocols
- Supports hundreds of individual application flows: Netflix, Oracle, SAP, Facebook, Zoom, Uber, and many more
- Covers Video Streaming, Social Media, SaaS, E-Commerce, Finance, Gaming, Chat, Web Conferencing
- Emulates thousands of endpoints with unique MAC and IP addresses
- Measures the impact of QoS policies on application performance and end-user experience
- Supports 130+ pre-defined and customisable layer 2–7 header templates: IPv4, IPv6, TCP, UDP, HTTP, SIP, RTP
- Multi-gig Ethernet: 1, 2.5, 5, and 10 GigE
- RESTful API for automation; offline analyser for long-duration tests; CSV export

**Talk track:** "When you deploy a new next-gen firewall or SD-WAN, how do you prove it can handle your full application mix at production load? AppStorm does that in the lab before you go live."

---

### TrafficEngine — Raw Packet Level Performance

Generates line-rate stateless traffic for classical performance measurements.

Key capabilities:
- Generates line-rate stateless traffic, emulating millions of complex traffic flows
- Measures throughput, packet loss, min/max/avg latency, jitter
- Tests network performance, QoS policies, and resiliency at scale
- Benchmarks raw packet-level performance: throughput, packet loss, latency, jitter
- 130+ customisable layer 2–7 header templates

**Talk track:** "Before you sign off on any new network hardware, you need to know its real throughput and latency under load. TrafficEngine gives you the independent benchmark — not just the vendor's spec sheet."

---

### SessionStrike — Stateful Device Session Capacity

Tests how fast stateful network devices can establish and hold TCP/HTTP connections.

Key capabilities:
- Creates millions of TCP and HTTP connections simultaneously
- Emulates clients and application servers at very high scale
- Verifies session holding capabilities of firewalls, load balancers, and other stateful devices
- Tests HTTP capacity with persistent connections and concurrent TCP connection capacity

**Talk track:** "Stateful devices have a hard limit on concurrent sessions. SessionStrike finds that limit in the lab so you don't find it in production during a traffic spike."

---

### AppPlayback — Production Traffic Reproduction

Captures real production traffic and replays it at scale in the lab.

Key capabilities:
- Captures production network conditions and converts them into dynamic traffic configurations
- Records and replays real-world traffic and application characteristics at scale
- Amplifies single flows into thousands for load testing
- Loads and replays up to 30,000 PCAPs (including large files >5 GB)
- Identifies and pinpoints packets causing performance errors or device crashes
- Multi-dimensional testing for faster fault analysis and architecture validation

**Talk track:** "Your test traffic is only as realistic as your production traffic. AppPlayback captures what's actually on your network and lets you replay it — at 10x scale — in a safe lab environment."

---

### Netropy CyberAttack — Cyber Security Emulation

A complete security test solution for validating network security architecture, DDoS defences, and security device performance.

Key capabilities:
- Simulates real-world cyber attacks simultaneously with legitimate application traffic
- Attack types: DDoS (large-scale), malware, CVEs (zero-day and known), XSS, MITM, SQL injection, DoS, brute force, spoofing
- Layer 2–7 attack coverage
- Evergreen Attack Library updated continuously with current threat intelligence
- CVE search by vendor name, CVE number, or attack type
- Generates traffic originating from specific geographic regions (for geo-based policy testing)
- Evaluates WAF, DDoS protection, next-gen firewalls, IPS/IDS systems
- Drag-and-drop traffic mix builder for complex scenarios in seconds

Available form factors:

| Model | Speed | Ports |
|-------|-------|-------|
| CyberAttack N61 | 1 Gbps | 2x RJ45 |
| CyberAttack 10G2 | 10 Gbps | 4x SFP+ |
| CyberAttack 10G4 | 10 Gbps | 8x SFP+ |
| CyberAttack 100G | 100 Gbps | 2x QSFP28 |
| CyberAttack 100G2 | 100 Gbps | 4x QSFP28 |
| Virtual Edition | — | VMware, KVM, Docker |
| Cloud Edition | — | AWS, Google Cloud, Azure |

All models: browser-based GUI (platform agnostic), RESTful API with Swagger, no client software.

**Talk track:** "Can you prove your security stack would stop the attack that hit JLR or the NHS? CyberAttack lets you run those exact attack patterns — DDoS, malware, CVEs — against your defences in a lab before a real attacker does."

---

## Combined emulator + traffic generator pitch

The strongest ANS pitch pairs an emulator with a traffic generator: emulate the WAN conditions (latency, loss, jitter) *while* generating realistic or adversarial application traffic. This is how production environments actually behave. No competitor bundles both from a single vendor with a single API.

---

## Lab maturity signals for traffic generators

**No traffic generator visible:** prospect tests with vendor-supplied tools or not at all. Entry angle: "how do you benchmark independently of the vendor's spec sheet?"

**Has a traffic generator from a legacy vendor (Ixia, Spirent):** look for EOL risk, cost of maintenance, complexity of operation. Entry angle: CyberAttack's ease-of-use, modern feature development cadence, and no EOL risk.

**Cyber posture programme visible (CISO hire, zero-trust language in job postings, post-incident press):** CyberAttack is the primary angle. Reference the JLR case study.

**Cloud or virtualisation programme visible:** Virtual and Cloud Editions of CyberAttack remove hardware procurement friction — strong angle for budget-constrained or fast-moving teams.
