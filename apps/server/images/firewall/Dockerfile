# syntax=docker/dockerfile:1.29

# One-shot container that installs the sandbox egress rules in the host network namespace.
FROM alpine:3.24.4
RUN apk add --no-cache iptables ip6tables nftables
COPY --chmod=0555 apply.sh /usr/local/bin/apply-sandbox-firewall
ENTRYPOINT ["/usr/local/bin/apply-sandbox-firewall"]
