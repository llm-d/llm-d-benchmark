package main

import (
	"fmt"

	"github.com/neuralmagic/llm-d-benchmark/hello"
)

func main() {
	// if len(os.Args) > 1 && os.Args[1] == "idle" {
	// 	// Stay alive forever
	// 	for {
	// 		time.Sleep(10 * time.Second)
	// 	}
	// }

	greeter := hello.NewGreeter("Hello")
	fmt.Println(greeter.Greet("Alice"))
	fmt.Println(greeter.Greet("Test"))
	fmt.Println(greeter.Greet("  Bob  "))
	fmt.Println(greeter.Greet(""))
}
