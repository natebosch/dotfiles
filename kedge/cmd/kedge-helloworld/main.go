package main

import (
	"fmt"
	"os"
)

func main() {
	fmt.Println("Hello, World from kedge-helloworld!")
	if len(os.Args) > 1 {
		fmt.Printf("Arguments: %v\n", os.Args[1:])
	}
}
