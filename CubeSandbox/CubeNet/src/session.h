// SPDX-License-Identifier: (GPL-2.0-only OR BSD-2-Clause)
/* Copyright (c) 2025 Cube Authors */
#ifndef __SESSION_H
#define __SESSION_H

#include <vmlinux.h>
#include "cubevs.h"
#include "map.h"

/* Lazy refresh threshold: 1 second in nanoseconds */
#define SESSION_REFRESH_INTERVAL_NS (1000 * 1000 * 1000UL)

/**
 * session_lazy_refresh - refresh session access time if stale
 * @sess:   pointer to the NAT session
 * @now_ns: current monotonic time in nanoseconds
 */
static __always_inline void session_lazy_refresh(struct nat_session *sess, __u64 now_ns)
{
	if (now_ns - sess->access_time > SESSION_REFRESH_INTERVAL_NS)
		sess->access_time = now_ns;
}

/**
 * session_mark_replied - transition simple UNREPLIED -> REPLIED state
 * @dir:             IP_CT_DIR_ORIGINAL or IP_CT_DIR_REPLY
 * @sess:            pointer to the NAT session
 * @unreplied_state: the protocol-specific UNREPLIED state value
 * @replied_state:   the protocol-specific REPLIED state value
 */
static __always_inline void session_mark_replied(enum ip_conntrack_dir dir,
						 struct nat_session *sess,
						 __u8 unreplied_state,
						 __u8 replied_state)
{
	if (dir == IP_CT_DIR_REPLY && sess->state == unreplied_state)
		sess->state = replied_state;
}

/**
 * create_nat_session - create egress session with rollback on failure
 * @ekey:          egress session key
 * @now_ns:        current monotonic time
 * @vm_ifindex:    TAP ifindex of the originating MVM
 * @snat_ip:       selected SNAT IP entry
 * @snat_port:     selected SNAT port/identifier in network byte order
 * @initial_state: protocol-specific initial conntrack state
 *
 * Returns true on success, false otherwise (ingress session cleaned up).
 */
static __always_inline bool create_nat_session(struct session_key *ekey,
					       __u64 now_ns, __u32 vm_ifindex,
					       struct snat_ip *snat_ip, __u16 snat_port,
					       __u8 initial_state)
{
	struct nat_session sess = {};
	struct session_key ikey = {};
	long err;

	sess.access_time = now_ns;
	sess.node_ifindex = snat_ip->ifindex;
	sess.node_ip = snat_ip->ip;
	sess.vm_ifindex = vm_ifindex;
	sess.vm_ip = ekey->src_ip;
	sess.node_port = snat_port;
	sess.vm_port = ekey->src_port;
	sess.state = initial_state;
	err = bpf_map_update_elem(&egress_sessions, ekey, &sess, BPF_NOEXIST);
	if (err) {
		/* on failure, clean up the ingress slot we reserved earlier */
		ikey.src_ip = ekey->dst_ip;
		ikey.dst_ip = snat_ip->ip;
		ikey.src_port = ekey->dst_port;
		ikey.dst_port = snat_port;
		ikey.version = 0;
		ikey.protocol = ekey->protocol;
		bpf_map_delete_elem(&ingress_sessions, &ikey);
		return false;
	}

	return true;
}

#endif /* __SESSION_H */
