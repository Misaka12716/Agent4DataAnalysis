// SPDX-License-Identifier: (GPL-2.0-only OR BSD-2-Clause)
/* Copyright (c) 2025 Cube Authors */
#ifndef __L2L3_H
#define __L2L3_H

#include <vmlinux.h>
#include "cubevs.h"

/**
 * rewrite_l3_addr - update an IP address in the IP header and fix the L3 checksum
 * @l3:       pointer to IP header (must be writable)
 * @addr_ptr: pointer to the address field to rewrite (e.g. &l3->saddr or &l3->daddr)
 * @new_addr: new IP address value
 */
static __always_inline void rewrite_l3_addr(struct iphdr *l3, __u32 *addr_ptr, __u32 new_addr)
{
	__u32 old_addr = *addr_ptr;
	__u32 old_csum = l3->check;
	__u32 new_csum;

	new_csum = bpf_csum_diff(&old_addr, sizeof(old_addr), &new_addr, sizeof(new_addr), ~old_csum);
	l3->check = csum_fold(new_csum);
	*addr_ptr = new_addr;
}

/**
 * set_mac_pair - overwrite both source and destination MAC addresses in L2 header
 * @l2:     pointer to Ethernet header
 * @src_p1: first 4 bytes of source MAC
 * @src_p2: last 2 bytes of source MAC
 * @dst_p1: first 4 bytes of destination MAC
 * @dst_p2: last 2 bytes of destination MAC
 */
static __always_inline void set_mac_pair(struct ethhdr *l2,
					 __u32 src_p1, __u16 src_p2,
					 __u32 dst_p1, __u16 dst_p2)
{
	union macaddr *macaddr;

	macaddr = (union macaddr *)l2->h_source;
	macaddr->p1 = src_p1;
	macaddr->p2 = src_p2;
	macaddr = (union macaddr *)l2->h_dest;
	macaddr->p1 = dst_p1;
	macaddr->p2 = dst_p2;
}

#endif /* __L2L3_H */
