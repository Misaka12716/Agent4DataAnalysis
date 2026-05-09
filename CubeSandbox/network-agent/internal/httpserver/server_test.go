// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package httpserver

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"

	"github.com/tencentcloud/CubeSandbox/network-agent/internal/service"
)

func TestHealthHandler(t *testing.T) {
	s := New("127.0.0.1:0", service.NewNoopService())
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	w := httptest.NewRecorder()
	s.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("healthz status=%d, want=%d", w.Code, http.StatusOK)
	}
	if body := w.Body.String(); body != "ok" {
		t.Fatalf("healthz body=%q, want=%q", body, "ok")
	}
}

func TestEnsureHandler(t *testing.T) {
	s := New("127.0.0.1:0", service.NewNoopService())
	req := httptest.NewRequest(http.MethodPost, "/v1/network/ensure", bytes.NewBufferString(`{"sandboxID":"sb-1"}`))
	w := httptest.NewRecorder()
	s.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("ensure status=%d, want=%d", w.Code, http.StatusOK)
	}
}

func TestNewEndpointUnix(t *testing.T) {
	socketPath := filepath.Join(t.TempDir(), "network-agent.sock")
	s, err := NewEndpoint("unix://"+socketPath, service.NewNoopService())
	if err != nil {
		t.Fatalf("NewEndpoint error=%v", err)
	}
	go func() {
		_ = s.Start()
	}()
	time.Sleep(20 * time.Millisecond)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := s.Stop(ctx); err != nil {
		t.Fatalf("Stop error=%v", err)
	}
}
