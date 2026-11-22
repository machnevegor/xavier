#!/bin/sh
# Install egress rules for the sandbox bridge. Idempotent; touches only traffic entering
# from that bridge. Sandboxes may reach the public internet but not private, reserved or
# host addresses, and not the host itself.
#
# With Docker's iptables backend the rules live in our own chains jumped to from DOCKER-USER
# and INPUT. With the nftables backend Docker has no DOCKER-USER chain, so the rules live in
# our own table whose base chains run before Docker's (a drop is final in either case).
set -eu

BRIDGE="${XAVIER_SANDBOX_BRIDGE:-xv-sandbox}"
BLOCKED_V4="0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 \
192.0.0.0/24 192.0.2.0/24 192.88.99.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 \
203.0.113.0/24 224.0.0.0/4 240.0.0.0/4"

# Create or flush chain $2 in tool $1.
reset_chain() {
    "$1" -w -N "$2" 2>/dev/null || "$1" -w -F "$2"
}

# Insert a jump from built-in or Docker chain $2 to chain $3 for the bridge, once.
ensure_jump() {
    "$1" -w -C "$2" -i "$BRIDGE" -j "$3" 2>/dev/null || "$1" -w -I "$2" -i "$BRIDGE" -j "$3"
}

apply_iptables() {
    reset_chain iptables XAVIER-SANDBOX
    for net in $BLOCKED_V4; do
        iptables -w -A XAVIER-SANDBOX -d "$net" -j DROP
    done
    iptables -w -A XAVIER-SANDBOX -j RETURN
    ensure_jump iptables DOCKER-USER XAVIER-SANDBOX

    reset_chain iptables XAVIER-SANDBOX-IN
    iptables -w -A XAVIER-SANDBOX-IN -j DROP
    ensure_jump iptables INPUT XAVIER-SANDBOX-IN

    # The sandbox network has no IPv6; drop any IPv6 from the bridge regardless.
    if ip6tables -w -n -L FORWARD >/dev/null 2>&1; then
        reset_chain ip6tables XAVIER-SANDBOX
        ip6tables -w -A XAVIER-SANDBOX -j DROP
        ensure_jump ip6tables FORWARD XAVIER-SANDBOX
        ensure_jump ip6tables INPUT XAVIER-SANDBOX
    fi
    echo "sandbox firewall: iptables rules installed for $BRIDGE"
}

apply_nftables() {
    blocked=$(echo $BLOCKED_V4 | tr ' ' ',')
    nft -f - <<EOF
table inet xavier_sandbox
delete table inet xavier_sandbox
table inet xavier_sandbox {
    chain forward {
        type filter hook forward priority filter - 10; policy accept;
        iifname "$BRIDGE" ip daddr { $blocked } drop
        iifname "$BRIDGE" meta nfproto ipv6 drop
    }
    chain input {
        type filter hook input priority filter - 10; policy accept;
        iifname "$BRIDGE" drop
    }
}
EOF
    echo "sandbox firewall: nftables table inet xavier_sandbox installed for $BRIDGE"
}

if iptables -w -n -L DOCKER-USER >/dev/null 2>&1; then
    apply_iptables
else
    apply_nftables
fi
