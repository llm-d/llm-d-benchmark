package hello_test

import (
	"testing"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
)

func TestHello(t *testing.T) {
	RegisterFailHandler(Fail)
	RunSpecs(t, "Hello Suite")
}
