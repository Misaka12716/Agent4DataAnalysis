// SPDX-License-Identifier: (GPL-2.0-only OR BSD-2-Clause)
/* Copyright (c) 2022 Cube Authors */
#ifndef __MAP_H
#define __MAP_H

#include "cubevs.h"

/* MVM IP to ifindex (managed by upper layer)
 *
 * key:   IP address in network byte order assigned to MVM
 * value: ifindex of the TAP device assigned to MVM
 */
struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, MAX_ENTRIES);
	__type(key, __u32);
	__type(value, __u32);
	__uint(pinning, LIBBPF_PIN_BY_NAME);
} mvmip_to_ifindex SEC(".maps");

/* ifindex to MVM metadata (managed by upper layer), we use IP/tunnel group ID only
 *
 * key:   ifindex of the TAP device assigned to MVM
 * value: tunnel group ID, ID and IP address in network byte order assigned to MVM
 */
struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, MAX_ENTRIES);
	__type(key, __u32);
	__type(value, struct mvm_meta);
	__uint(pinning, LIBBPF_PIN_BY_NAME);
} ifindex_to_mvmmeta SEC(".maps");

/* host port (for remote access from CubeProxy) to MVM port mapping
 *
 * key:   host port
 * value: MVM ifindex + MVM listen port
 */
struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, MAX_PORTS);
	__type(key, __u16);
	__type(value, struct mvm_port);
	__uint(pinning, LIBBPF_PIN_BY_NAME);
} remote_port_mapping SEC(".maps");

/* MVM port (for NAT) to host port mapping
 *
 * key:   MVM ifindex + MVM listen port
 * value: host port
 */
struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, MAX_PORTS);
	__type(key, struct mvm_port);
	__type(value, __u16);
	__uint(pinning, LIBBPF_PIN_BY_NAME);
} local_port_mapping SEC(".maps");

/* Egress session table
 *
 * key:   5-tuple for egress packet
 * value: session
 */
struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, MAX_SESSIONS);
	__type(key, struct session_key);
	__type(value, struct nat_session);
	__uint(pinning, LIBBPF_PIN_BY_NAME);
} egress_sessions SEC(".maps");

/* Ingress session table
 *
 * key:   5-tuple for ingress packet
 * value: used to construct lookup key for egress_sessions
 */
struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, MAX_SESSIONS);
	__type(key, struct session_key);
	__type(value, struct ingress_session);
	__uint(pinning, LIBBPF_PIN_BY_NAME);
} ingress_sessions SEC(".maps");

/* SNAT IP list
 *
 * key:   index for hash(MVM_IP)
 * value: SNAT IP and its ifindex, max_port
 */
struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, MAX_SNAT_IPS);
	__type(key, __u32);
	__type(value, struct snat_ip);
	__uint(pinning, LIBBPF_PIN_BY_NAME);
} snat_iplist SEC(".maps");

/* Inner map template for network policy (LPM trie)
 *
 * key:   struct lpm_key (prefixlen + IP)
 * value: __u32 (action / placeholder)
 */
struct {
	__uint(type, BPF_MAP_TYPE_LPM_TRIE);
	__uint(max_entries, MAX_ENTRIES);
	__type(key, struct lpm_key);
	__type(value, __u32);
	__uint(map_flags, BPF_F_NO_PREALLOC);
} net_policy_inner SEC(".maps");

/* Egress allow list (hash of maps)
 *
 * key:   ifindex of the TAP device
 * value: fd of inner LPM trie map (destination IP allow list)
 *
 * If the inner map exists for a given ifindex and the destination IP
 * matches an entry, the packet is allowed regardless of deny_out.
 */
struct {
	__uint(type, BPF_MAP_TYPE_HASH_OF_MAPS);
	__uint(max_entries, MAX_ENTRIES);
	__type(key, __u32);
	__uint(pinning, LIBBPF_PIN_BY_NAME);
	__array(values, struct {
		__uint(type, BPF_MAP_TYPE_LPM_TRIE);
		__uint(max_entries, MAX_ENTRIES);
		__type(key, struct lpm_key);
		__type(value, __u32);
		__uint(map_flags, BPF_F_NO_PREALLOC);
	});
} allow_out SEC(".maps");

/* Egress deny list (hash of maps)
 *
 * key:   ifindex of the TAP device
 * value: fd of inner LPM trie map (destination IP deny list)
 *
 * If the inner map exists for a given ifindex and the destination IP
 * matches an entry, the packet is denied.
 */
struct {
	__uint(type, BPF_MAP_TYPE_HASH_OF_MAPS);
	__uint(max_entries, MAX_ENTRIES);
	__type(key, __u32);
	__uint(pinning, LIBBPF_PIN_BY_NAME);
	__array(values, struct {
		__uint(type, BPF_MAP_TYPE_LPM_TRIE);
		__uint(max_entries, MAX_ENTRIES);
		__type(key, struct lpm_key);
		__type(value, __u32);
		__uint(map_flags, BPF_F_NO_PREALLOC);
	});
} deny_out SEC(".maps");

#endif /* __MAP_H */
