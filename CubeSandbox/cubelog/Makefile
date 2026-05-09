
SOURCE:=$(shell ./test.sh)
.PHONY: test

version:
	go version

unittest: version
	#@export GO111MODULE=off
	#@export GOPROXY=https://mirrors.tencent.com/go/
	#@export GOPATH=/root/go
	#go test -v -gcflags=-l  .
	echo $(SOURCE);gotest -v -gcflags=-l -coverprofile=size_coverage.out .;
	#go tool cover -func=size_coverage.out
	@go tool cover -html=size_coverage.out


outtest:
	#go vet
	gotests -all -w *.go
