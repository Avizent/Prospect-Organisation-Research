# ANS Product Knowledge — Network Emulators

**Vendor:** Apposite Technologies (ANS is Authorised Distributor and Official Reseller)
**Product lines:** Linktropy series, Netropy series

---

## What network emulators do

WAN emulators create a controlled lab environment that replicates real-world wide area network conditions. They sit in-line between client and server networks and inject precise impairments so engineers can test how applications behave before deployment — without buying expensive live satellite time, live WAN links, or dispatching technicians to remote sites.

Core impairments simulated:
- **Bandwidth:** 100 bps to 100 Gbps (model dependent)
- **Latency:** 0 ms to 10,000 ms (up to 20 seconds on Netropy models), with distributions: constant, uniform, normal, exponential, accumulate & burst
- **Jitter:** propagation, processing, transmission, and queuing delay components
- **Packet loss:** random, burst, periodic, Bit Error Rate (BER), Gilbert-Elliott model
- **Packet corruption, reordering, duplication**
- **Network outages** (full link drops)
- **Background traffic / congestion:** PCAP replay to simulate real utilisation
- **Multiple simultaneous links:** up to 15–30 virtual WAN links per device pair, each with independent impairments

---

## Product range

### Linktropy Mini Series (Mini2, Mini-G, Mini-G100)
- Portable, low-cost
- Up to 1 Gbps emulation
- Silent operation (fanless)
- Ideal for: application development, customer demonstrations, road warriors

### Linktropy Series (5510, 8510)
- Up to 1 Gbps
- 8510 supports multiple links
- Ideal for: product testing, high-precision benchmarking

### Netropy Network Emulators
Enterprise-class. Advanced modelling, multi-user admin, complex multi-link scenarios.

| Model | Speed | Notes |
|-------|-------|-------|
| N61 | Up to 1 Gbps | Entry-level enterprise |
| N91 | Up to 1 Gbps | Mid-range enterprise |
| 10G1 | Up to 10 Gbps | 10G single port pair |
| 10G2 | Up to 10 Gbps | 10G two port pairs |
| 10G4 | Up to 10 Gbps | 10G four port pairs |
| 100G | Up to 100 Gbps | Supports 25/40 Gbps |

All Netropy models: browser-based GUI, RESTful API for automation, quick install (minutes), no host software required.

---

## Key selling points

**Quick to configure.** Tests set up in minutes. Browser-based GUI — no client software. Same interface across all models.

**Precise and repeatable.** Parameters saved and reloaded. Live network conditions captured and replayed exactly. Deterministic results across test runs.

**Satellite testing.** Netropy simulates GEO, LEO, and MEO satellite orbits. Supports the Gilbert-Elliott model for burst error patterns from atmospheric conditions (rain fade, weather degradation). Critical differentiator for defence, maritime, oil & gas, and government customers.

**Cost reduction.** Satellite time costs up to $1,000 to book, up to $1/min to use — $100,000+ for a full test programme. Netropy eliminates that. WAN emulators start at ~$2,000.

**Automation.** RESTful API for integration with CI/CD pipelines, test automation frameworks, and DevOps workflows.

---

## Use cases (talk tracks for specific verticals)

**SD-WAN selection and benchmarking:** Simulate how SD-WAN systems respond to impairments — latency spikes, jitter, link aggregation, congestion — before purchasing. Validates traffic prioritisation, link failover, and application-aware routing. Strong angle for organisations evaluating or mid-deployment of SD-WAN.

**Data centre relocation / cloud migration:** Emulate network conditions from branch offices, home workers, and on-the-road users through private lines, internet VPNs, and cloud paths simultaneously. Identifies issues traditional local tests miss. Strong angle for organisations mid-migration or planning consolidation.

**VDI / remote work infrastructure:** Precisely emulate the conditions under which a new VDI deployment will run before committing to hardware. Vendor-agnostic — test multiple solutions side by side.

**VoIP and video quality:** Test call quality under real-world latency, jitter, and loss before rollout. Avoids post-deployment complaints.

**Application development and QA:** DevOps and QA teams run apps over emulated WAN as part of the CI/CD pipeline, catching performance regressions before they reach production.

**Defence / military:** Used by Lockheed Martin on the THAAD system — recreates high latency and packet loss for satellite-dependent weapon system communications. ANS has defence credibility to reference (non-confidential).

**Regulatory / compliance verticals (DORA, NIS2, HIPAA, FedRAMP):** Emulation testing can be documented as part of network resilience programmes required by these frameworks.

---

## Lab maturity signals (for needs inference agent)

A prospect with **no visible lab** likely relies on production testing — highest pain, easiest conversation. Entry angle: "how do you validate new applications before deployment?"

A prospect with a **basic lab** (physical kit, manual testing) can likely be upgraded to emulation-based automation. Entry angle: "how repeatable and scalable is your current process?"

A prospect with a **mature lab** may already have a competitor product. Entry angle: 10G/100G upgrades, satellite capability gaps, RESTful API for automation, and multilink complex scenarios their current kit can't handle.

A prospect in **modernisation** (SD-WAN, cloud, VDI programme visible in job postings or press) is in active buying mode. Urgency is real. Entry angle: the specific transformation they are running.
