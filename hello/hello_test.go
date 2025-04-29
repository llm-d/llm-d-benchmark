package hello_test

import (
	"github.com/neuralmagic/llm-d-benchmark/hello"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
)

var _ = Describe("Greeter", func() {
	Context("NewGreeter", func() {
		It("should default to 'Hello' when given an empty greeting", func() {
			g := hello.NewGreeter("")
			Expect(g.Greeting).To(Equal("Hello"))
		})

		It("should use the provided greeting", func() {
			g := hello.NewGreeter("Hi")
			Expect(g.Greeting).To(Equal("Hi"))
		})
	})

	Context("Greet", func() {
		var greeter *hello.Greeter

		BeforeEach(func() {
			greeter = hello.NewGreeter("Hello")
		})

		It("should greet with the given name", func() {
			Expect(greeter.Greet("Alice")).To(Equal("Hello, Alice!"))
		})

		It("should trim extra whitespace from the name", func() {
			Expect(greeter.Greet("  Bob  ")).To(Equal("Hello, Bob!"))
		})

		It("should default to 'World' when name is empty", func() {
			Expect(greeter.Greet("")).To(Equal("Hello, World!"))
		})

		Context("table-driven tests", func() {
			DescribeTable("various greetings",
				func(greeting, name, expected string) {
					g := hello.NewGreeter(greeting)
					Expect(g.Greet(name)).To(Equal(expected))
				},
				Entry("default greeting with a name", "Hello", "Charlie", "Hello, Charlie!"),
				Entry("custom greeting with a name", "Hi", "Dana", "Hi, Dana!"),
				Entry("default greeting with empty name", "Hello", "", "Hello, World!"),
				Entry("trimming whitespace", "Welcome", "  Eve  ", "Welcome, Eve!"),
			)
		})
	})
})
