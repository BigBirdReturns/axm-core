//go:build js && wasm

package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	pack "github.com/BigBirdReturns/axm-core/knowledgepack/internal/pack"
	"syscall/js"
)

var exported js.Func

func respond(this js.Value, args []js.Value) (value any) {
	defer func() {
		if r := recover(); r != nil {
			b, _ := json.Marshal(map[string]any{"error": fmt.Sprint(r)})
			value = string(b)
		}
	}()
	var out map[string]any
	var err error
	if len(args) != 4 {
		return `{ "error":"Invalid argument count" }`
	}
	if args[0].String() == "vector" {
		a, e := base64.StdEncoding.Strict().DecodeString(args[1].String())
		if e != nil {
			err = e
		} else {
			k, e := base64.StdEncoding.Strict().DecodeString(args[2].String())
			if e != nil {
				err = e
			} else {
				out, err = pack.VerifyVector(a, k)
			}
		}
	} else {
		out, err = pack.Request(args[0].String(), args[1].String(), args[2].String(), args[3].String())
	}
	if err != nil {
		out = map[string]any{"error": err.Error()}
	}
	b, e := json.Marshal(out)
	if e != nil {
		return `{ "error":"Result encoding failed" }`
	}
	return string(b)
}
func main() { exported = js.FuncOf(respond); js.Global().Set("axmVerify", exported); select {} }
