package hello

import "strings"

// Greeter represents a greeting configuration.
type Greeter struct {
	Greeting string
}

// NewGreeter creates a new Greeter with a given greeting prefix.
// If greeting is empty, it defaults to "Hello".
func NewGreeter(greeting string) *Greeter {
	if greeting == "" {
		greeting = "Hello"
	}
	return &Greeter{Greeting: greeting}
}

// Greet returns a greeting for the provided name.
// It trims whitespace from the name; if empty after trimming, it defaults to "World".
func (g *Greeter) Greet(name string) string {
	trimmed := strings.TrimSpace(name)
	if trimmed == "" {
		trimmed = "World"
	}
	return g.Greeting + ", " + trimmed + "!"
}
