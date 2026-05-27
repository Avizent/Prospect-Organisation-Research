# ANS Case Studies and Reference Accounts

> **Note:** These are reference stories drawn from public sources and ANS materials. Use them to illustrate credibility in sales documents. Do not fabricate metrics or add detail not listed here. If you cannot verify a claim, mark it as "claimed" and note the source.

---

## Lockheed Martin — THAAD Anti-Ballistic Missile Defence System

**Industry:** Defence / Aerospace
**Use case:** WAN emulation for satellite communication testing
**Product:** Netropy WAN emulator

Lockheed Martin uses Netropy emulators to test the Terminal High Altitude Area Defence (THAAD) system. The system depends on satellite communication between control, tracking, and firing subsystems — situations where "seconds are critical." Netropy recreates high latency and packet loss in the lab to troubleshoot issues before field deployment, eliminating the risk and cost of testing on live systems.

**Relevance for sales:** Strongest credibility reference for defence, government, satellite-dependent infrastructure, and mission-critical applications. The implicit message is: if it's trusted for missile defence, it's trusted for your network.

---

## Mobile Labs — Mobile Application Testing

**Industry:** Technology / Mobile software
**Use case:** Realistic mobile network condition simulation for app QA
**Product:** Netropy WAN emulator

Mobile Labs uses Netropy emulators to connect mobile devices via Wi-Fi to application servers during testing. Each emulator simulates up to 20 links simultaneously, each with unique impairments (different mobile network conditions per device), enabling realistic user experience testing at scale without needing to test on live mobile networks globally.

**Relevance for sales:** Strong reference for technology companies with mobile apps, QA teams, and DevOps/CI-CD audiences. The 20-simultaneous-links capability is a differentiator.

---

## JLR Cyber Attack (Industry Reference — CyberAttack angle)

**Industry:** Automotive / Manufacturing
**Use case:** Cyber threat scenario reference
**Product:** Netropy CyberAttack (illustrative — the actual JLR breach predates the ANS engagement)

A cyber attack on JLR's supply chain (via a compromised SAP system in a supplier network) illustrates the risk of untested security configurations. Analysis from ANS demonstrates that CyberAttack could have been used to simulate the attack vectors — including those targeting the supplier's infrastructure — to identify configuration weaknesses, segmentation flaws, and inadequate detection rules before the real attack occurred.

**Relevance for sales:** Strong reference for CISO conversations, automotive sector, supply chain security, and "prove your defences" positioning. Works best when paired with a specific regulatory or recent-incident angle for the target prospect.

---

## Generic use case references (for verticals without named accounts)

These are narrative patterns validated by the product, usable when no named customer reference applies:

**SD-WAN selection:** An enterprise selects between three SD-WAN vendors. Using Netropy, the IT team emulates their actual WAN conditions (mixed private MPLS + 4G backup + broadband failover) and runs each vendor's appliance through identical tests. They select the vendor whose appliance handles asymmetric latency most gracefully — a result that only emerged under emulated conditions, not vendor-provided demos.

**Cloud migration / data centre consolidation:** A retailer migrating from two data centres to a single cloud-hosted platform uses Netropy to emulate the network experience of branch office users before committing to the architecture. They identify an ERP module that performs unacceptably over the emulated cloud path and negotiate a protocol optimisation from the ERP vendor before migration day.

**Security posture validation:** A financial services firm, following NIS2 implementation, uses CyberAttack to run a programme of quarterly adversarial testing against their perimeter — DDoS, malware injection, CVE exploitation. The programme produces documented evidence of resilience testing for their regulatory submission.

---

## To add (user action required)

Replace or supplement the above with named customer references from ANS's own customer base. For each, capture:
- Industry and approximate company size
- The problem they had before ANS
- The specific product(s) used
- The measurable outcome (time saved, cost avoided, incidents prevented)
- Whether the customer can be referenced by name in documents

The agents will use whatever is in this file. Richer, named case studies produce materially better benefits documents.
