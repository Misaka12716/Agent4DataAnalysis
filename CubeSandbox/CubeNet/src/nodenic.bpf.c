// SPDX-License-Identifier: GPL-2.0
/* Copyright (c) 2022 Cube Authors */
#include <vmlinux.h>
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_endian.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

#include "cubevs.h"
#include "icmp.h"
#include "jhash.h"
#include "l2l3.h"
#include "map.h"
#include "skb.h"
#include "tcp.h"
#include "udp.h"

static int tcp_nat_proxy(struct ethhdr *l2, struct iphdr *l3, struct tcphdr *l4, struct mvm_port *mvm_port)
{
	__u32 old_csum, new_csum;
	struct csum_buff old_buff = {}, new_buff = {};

	/* update target port and L4 csum */
	old_buff.addr = l3->daddr;
	old_buff.port = l4->dest;
	new_buff.addr = mvm_inner_ip;
	new_buff.port = mvm_port->listen_port;
	old_csum = l4->check;
	new_csum = bpf_csum_diff((void *)&old_buff, sizeof(old_buff), (void *)&new_buff, sizeof(new_buff), ~old_csum);
	l4->check = csum_fold(new_csum);
	l4->dest = mvm_port->listen_port;

	/* update target addr and L3 csum */
	rewrite_l3_addr(l3, &l3->daddr, mvm_inner_ip);

	/* update L2 addrs */
	set_mac_pair(l2, cubegw0_macaddr_p1, cubegw0_macaddr_p2,
		     mvm_macaddr_p1, mvm_macaddr_p2);

	return bpf_redirect(mvm_port->ifindex, 0);
}

static __always_inline struct nat_session *lookup_session(const struct session_key *ikey)
{
	struct ingress_session *isess;
	struct session_key key = {};

	isess = bpf_map_lookup_elem(&ingress_sessions, ikey);
	if (!isess)
		return NULL;

	key.src_ip = isess->vm_ip;
	key.dst_ip = ikey->src_ip;
	key.src_port = isess->vm_port;
	key.dst_port = ikey->src_port;
	key.version = isess->version;
	key.protocol = ikey->protocol;
	return bpf_map_lookup_elem(&egress_sessions, &key);
}

static int tcp_nat_session(struct __sk_buff *skb, struct ethhdr *l2, struct iphdr *l3, struct tcphdr *l4)
{
	struct csum_buff old_buff = {}, new_buff = {};
	__u32 old_csum, new_csum;
	struct session_key key = {};
	struct nat_session *sess;
	bool syn, ack, fin, rst;
	__u64 now;

	key.src_ip = l3->saddr;
	key.dst_ip = l3->daddr;
	key.src_port = l4->source;
	key.dst_port = l4->dest;
	key.version = 0;
	key.protocol = l3->protocol;
	sess = lookup_session(&key);
	if (!sess)
		return TC_ACT_OK;

	/* update session */
	now = bpf_ktime_get_ns();
	syn = l4->syn;
	ack = l4->ack;
	fin = l4->fin;
	rst = l4->rst;
	update_session(IP_CT_DIR_REPLY, sess, now, syn, ack, fin, rst);

	/* update L4 */
	old_buff.addr = l3->daddr;
	old_buff.port = l4->dest;
	new_buff.addr = mvm_inner_ip;
	new_buff.port = sess->vm_port;
	old_csum = l4->check;
	new_csum = bpf_csum_diff((void *)&old_buff, sizeof(old_buff), (void *)&new_buff, sizeof(new_buff), ~old_csum);
	l4->check = csum_fold(new_csum);
	l4->dest = sess->vm_port;

	/* update L3 */
	rewrite_l3_addr(l3, &l3->daddr, mvm_inner_ip);

	/* update L2 */
	set_mac_pair(l2, cubegw0_macaddr_p1, cubegw0_macaddr_p2,
		     mvm_macaddr_p1, mvm_macaddr_p2);

	return bpf_redirect(sess->vm_ifindex, 0);
}

static int udp_nat_session(struct __sk_buff *skb, struct ethhdr *l2, struct iphdr *l3, struct udphdr *l4)
{
	struct csum_buff old_buff = {}, new_buff = {};
	__u32 old_csum, new_csum;
	struct session_key key = {};
	struct nat_session *sess;
	__u64 now;

	key.src_ip = l3->saddr;
	key.dst_ip = l3->daddr;
	key.src_port = l4->source;
	key.dst_port = l4->dest;
	key.version = 0;
	key.protocol = IPPROTO_UDP;
	sess = lookup_session(&key);
	if (!sess)
		return TC_ACT_OK;

	/* update session */
	now = bpf_ktime_get_ns();
	update_udp_session(IP_CT_DIR_REPLY, sess, now);

	/* update L4 */
	old_buff.addr = l3->daddr;
	old_buff.port = l4->dest;
	new_buff.addr = mvm_inner_ip;
	new_buff.port = sess->vm_port;
	old_csum = l4->check;
	if (old_csum) {
		new_csum = bpf_csum_diff((void *)&old_buff, sizeof(old_buff), (void *)&new_buff, sizeof(new_buff),
					 ~old_csum);
		l4->check = csum_fold(new_csum) ?: 0xffff;
	}
	l4->dest = sess->vm_port;

	/* update L3 */
	rewrite_l3_addr(l3, &l3->daddr, mvm_inner_ip);

	/* update L2 */
	set_mac_pair(l2, cubegw0_macaddr_p1, cubegw0_macaddr_p2,
		     mvm_macaddr_p1, mvm_macaddr_p2);

	return bpf_redirect(sess->vm_ifindex, 0);
}

static int icmp_nat_session(struct __sk_buff *skb, struct ethhdr *l2, struct iphdr *l3, struct icmphdr *l4)
{
	struct icmp_id_buff old_id = {}, new_id = {};
	__u32 old_csum, new_csum;
	struct session_key key = {};
	struct nat_session *sess;
	__u64 now;

	/* Only handle Echo Reply inbound */
	if (l4->type != ICMP_ECHOREPLY)
		return TC_ACT_OK;

	/* ingress key: src=remote, dst=node_ip, src_port=0, dst_port=identifier */
	key.src_ip = l3->saddr;
	key.dst_ip = l3->daddr;
	key.src_port = 0;
	key.dst_port = l4->un.echo.id; /* the SNAT identifier we assigned */
	key.version = 0;
	key.protocol = IPPROTO_ICMP;
	sess = lookup_session(&key);
	if (!sess)
		return TC_ACT_OK;

	/* update session */
	now = bpf_ktime_get_ns();
	update_icmp_session(IP_CT_DIR_REPLY, sess, now);

	/* restore original ICMP identifier (no pseudo-header).
	 * bpf_csum_diff requires 4-byte aligned sizes, use icmp_id_buff.
	 */
	old_id.id = l4->un.echo.id;
	new_id.id = sess->vm_port;
	old_csum = l4->checksum;
	new_csum = bpf_csum_diff((void *)&old_id, sizeof(old_id), (void *)&new_id, sizeof(new_id), ~old_csum);
	l4->checksum = csum_fold(new_csum) ?: 0xffff;
	l4->un.echo.id = sess->vm_port;

	/* update L3 destination address */
	rewrite_l3_addr(l3, &l3->daddr, mvm_inner_ip);

	/* update L2 */
	set_mac_pair(l2, cubegw0_macaddr_p1, cubegw0_macaddr_p2,
		     mvm_macaddr_p1, mvm_macaddr_p2);

	return bpf_redirect(sess->vm_ifindex, 0);
}

static int do_icmp_nat(struct __sk_buff *skb)
{
	struct ethhdr *l2;
	struct iphdr *l3;
	struct icmphdr *l4;

	if (!__pull_headers_icmp(skb, &l2, &l3, &l4))
		return TC_ACT_OK;

	return icmp_nat_session(skb, l2, l3, l4);
}

static int do_udp_nat(struct __sk_buff *skb)
{
	struct ethhdr *l2;
	struct iphdr *l3;
	struct udphdr *l4;

	if (!__pull_headers_udp(skb, &l2, &l3, &l4))
		return TC_ACT_OK;

	return udp_nat_session(skb, l2, l3, l4);
}

static int do_tcp_nat(struct __sk_buff *skb)
{
	struct mvm_port *mvm_port;
	struct ethhdr *l2;
	struct iphdr *l3;
	struct tcphdr *l4;
	__u16 dport;

	if (!__pull_headers(skb, &l2, &l3, &l4))
		return TC_ACT_OK;

	dport = l4->dest;
	mvm_port = bpf_map_lookup_elem(&remote_port_mapping, &dport);
	if (mvm_port)
		return tcp_nat_proxy(l2, l3, l4, mvm_port);

	return tcp_nat_session(skb, l2, l3, l4);
}

/* This filter will be attached to the ingress path of host NIC.
 * It performs NAT and then redirect the traffics to Sandbox TAP devices.
 */
SEC("tc")
int from_world(struct __sk_buff *skb)
{
	struct ethhdr *l2;
	struct iphdr *l3;
	int ret;

	if (skb->protocol != bpf_htons(ETH_P_IP))
		return TC_ACT_OK;

	ret = pull_headers(skb, &l2, &l3);
	if (ret != TC_ACT_OK)
		return TC_ACT_OK;

	if (l3->protocol == IPPROTO_TCP)
		return do_tcp_nat(skb);

	if (l3->protocol == IPPROTO_UDP)
		return do_udp_nat(skb);

	if (l3->protocol == IPPROTO_ICMP)
		return do_icmp_nat(skb);

	return TC_ACT_OK;
}

char __license[] SEC("license") = "Dual BSD/GPL";
