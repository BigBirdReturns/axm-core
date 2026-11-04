package main

import (
	"encoding/json"
	"fmt"
	pack "github.com/BigBirdReturns/axm-core/knowledgepack/internal/pack"
	"io"
	"os"
)

func fail(e error) {
	json.NewEncoder(os.Stdout).Encode(map[string]any{"status": "ERROR", "error": e.Error()})
	os.Exit(2)
}
func readBounded(name string, limit int64) ([]byte, error) {
	f, e := os.Open(name)
	if e != nil {
		return nil, e
	}
	defer f.Close()
	b, e := io.ReadAll(io.LimitReader(f, limit+1))
	if e == nil && int64(len(b)) > limit {
		return nil, fmt.Errorf("input exceeds byte limit")
	}
	return b, e
}
func main() {
	if len(os.Args) < 5 || os.Args[3] != "--trusted-key" {
		fmt.Fprintln(os.Stderr, "axm-pack-reader inspect PACK --trusted-key KEY [source|ed25519|mldsa|wrong-key]")
		os.Exit(2)
	}
	data, e := readBounded(os.Args[2], 8*1024*1024)
	if e != nil {
		fail(e)
	}
	key, e := readBounded(os.Args[4], 1344)
	if e != nil {
		fail(e)
	}
	var out map[string]any
	switch os.Args[1] {
	case "inspect":
		out, e = pack.Inspect(data, key)
	case "challenge":
		if len(os.Args) != 6 {
			fail(fmt.Errorf("challenge mode required"))
		}
		out, e = pack.Challenge(data, key, os.Args[5])
	case "vector":
		out, e = pack.VerifyVector(data, key)
	default:
		fail(fmt.Errorf("unknown command"))
	}
	if e != nil {
		fail(e)
	}
	json.NewEncoder(os.Stdout).Encode(out)
	if out["status"] != "PASS" {
		if os.Args[1] == "vector" {
			structural := true
			errors, _ := out["errors"].([]any)
			if len(errors) == 0 {
				structural = false
			}
			for _, raw := range errors {
				entry, _ := raw.(map[string]any)
				code, _ := entry["code"].(string)
				if code != "E_LAYOUT_MISSING" && code != "E_SCHEMA_MISSING" && code != "E_SIG_MISSING" {
					structural = false
				}
			}
			if structural {
				os.Exit(2)
			}
		}
		os.Exit(1)
	}
}
