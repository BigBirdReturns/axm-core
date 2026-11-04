package pack

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRealPack(t *testing.T) {
	root := filepath.Join("..", "..", "..", "fixtures")
	a, e := os.ReadFile(filepath.Join(root, "example.axmpack.zip"))
	if e != nil {
		t.Fatal(e)
	}
	k, e := os.ReadFile(filepath.Join(root, "trusted-demo.pub"))
	if e != nil {
		t.Fatal(e)
	}
	v, e := Inspect(a, k)
	if e != nil || v["status"] != "PASS" {
		t.Fatalf("open: %v %v", v, e)
	}
	for _, m := range []string{"source", "ed25519", "mldsa", "wrong-key"} {
		t.Run(m, func(t *testing.T) {
			v, e := Challenge(a, k, m)
			if e != nil || v["status"] != "FAIL" {
				t.Fatalf("challenge: %v %v", v, e)
			}
		})
	}
}
