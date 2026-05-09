// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package service

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type persistedState struct {
	SandboxID       string            `json:"sandboxID"`
	NetworkHandle   string            `json:"networkHandle"`
	TapName         string            `json:"tapName"`
	TapIfIndex      int               `json:"tapIfIndex"`
	SandboxIP       string            `json:"sandboxIP"`
	Interfaces      []Interface       `json:"interfaces"`
	Routes          []Route           `json:"routes"`
	ARPNeighbors    []ARPNeighbor     `json:"arpNeighbors"`
	PortMappings    []PortMapping     `json:"portMappings"`
	CubeVSContext   *CubeVSContext    `json:"cubevsContext,omitempty"`
	PersistMetadata map[string]string `json:"persistMetadata"`
}

type stateStore struct {
	dir string
}

func newStateStore(dir string) (*stateStore, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	return &stateStore{dir: dir}, nil
}

func (s *stateStore) Save(state *persistedState) error {
	if state == nil {
		return fmt.Errorf("state is nil")
	}
	p, err := s.path(state.SandboxID)
	if err != nil {
		return err
	}
	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(p, data, 0o644) // NOCC:Path Traversal()
}

func (s *stateStore) Delete(sandboxID string) error {
	p, err := s.path(sandboxID)
	if err != nil {
		return err
	}
	err = os.Remove(p)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

func (s *stateStore) LoadAll() ([]*persistedState, error) {
	entries, err := os.ReadDir(s.dir)
	if err != nil {
		return nil, err
	}
	sort.Slice(entries, func(i, j int) bool {
		return entries[i].Name() < entries[j].Name()
	})
	var states []*persistedState
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		data, err := os.ReadFile(filepath.Join(s.dir, entry.Name()))
		if err != nil {
			return nil, err
		}
		state := &persistedState{}
		if err := json.Unmarshal(data, state); err != nil {
			return nil, err
		}
		states = append(states, state)
	}
	return states, nil
}

func (s *stateStore) path(sandboxID string) (string, error) {
	if strings.ContainsAny(sandboxID, `/\.`) || sandboxID == "" {
		return "", fmt.Errorf("invalid sandboxID %q: contains path separators or traversal characters", sandboxID)
	}
	return filepath.Join(s.dir, sandboxID+".json"), nil
}
