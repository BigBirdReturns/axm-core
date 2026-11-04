package pack

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	axm "github.com/BigBirdReturns/axm-core/knowledgepack/internal/genesis"
	"io"
	"io/fs"
	"lukechampine.com/blake3"
	"regexp"
	"sort"
	"strings"
	"time"
)

const format = "axm-pack-preview/0.1"
const maxArchive = 8 * 1024 * 1024
const maxTotal = 32 * 1024 * 1024

var segment = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._@-]*$`)
var shardRE = regexp.MustCompile(`^sh1_[0-9a-f]{64}$`)
var deviceRE = regexp.MustCompile(`(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)`)

type obj = map[string]any

func digest(data []byte) string   { h := sha256.Sum256(data); return hex.EncodeToString(h[:]) }
func identity(data []byte) string { h := blake3.Sum256(data); return "sh1_" + hex.EncodeToString(h[:]) }
func strictJSON(raw []byte) (any, error) {
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var parse func() (any, error)
	parse = func() (any, error) {
		tok, e := d.Token()
		if e != nil {
			return nil, e
		}
		if delim, ok := tok.(json.Delim); ok {
			switch delim {
			case '{':
				m := obj{}
				for d.More() {
					k, e := d.Token()
					if e != nil {
						return nil, e
					}
					key, ok := k.(string)
					if !ok {
						return nil, errors.New("Invalid JSON key")
					}
					if _, ok = m[key]; ok {
						return nil, errors.New("Duplicate JSON key")
					}
					v, e := parse()
					if e != nil {
						return nil, e
					}
					m[key] = v
				}
				_, e = d.Token()
				return m, e
			case '[':
				a := []any{}
				for d.More() {
					v, e := parse()
					if e != nil {
						return nil, e
					}
					a = append(a, v)
				}
				_, e = d.Token()
				return a, e
			default:
				return nil, errors.New("Unexpected JSON delimiter")
			}
		}
		return tok, nil
	}
	v, e := parse()
	if e != nil {
		return nil, e
	}
	if _, e = d.Token(); e != io.EOF {
		return nil, errors.New("Trailing JSON content")
	}
	return v, nil
}
func stringArray(v any) ([]string, error) {
	a, ok := v.([]any)
	if !ok {
		return nil, errors.New("Expected string list")
	}
	out := []string{}
	for _, x := range a {
		s, ok := x.(string)
		if !ok {
			return nil, errors.New("Expected string list")
		}
		out = append(out, s)
	}
	return out, nil
}
func asObject(raw []byte) (obj, error) {
	v, e := strictJSON(raw)
	if e != nil {
		return nil, e
	}
	m, ok := v.(obj)
	if !ok {
		return nil, errors.New("Expected JSON object")
	}
	return m, nil
}
func unpack(data []byte) (map[string][]byte, obj, error) {
	if len(data) > maxArchive {
		return nil, nil, errors.New("Archive exceeds 8 MiB")
	}
	z, e := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if e != nil {
		return nil, nil, e
	}
	if len(z.File) == 0 || len(z.File) > 1024 {
		return nil, nil, errors.New("Archive must contain 1 to 1024 members")
	}
	files := map[string][]byte{}
	folded := map[string]bool{}
	var total uint64
	for _, f := range z.File {
		parts := strings.Split(f.Name, "/")
		if len(parts) > 8 || len(f.Name) > 220 {
			return nil, nil, errors.New("Unsafe archive path")
		}
		for _, p := range parts {
			if !segment.MatchString(p) || strings.HasSuffix(p, ".") || deviceRE.MatchString(p) {
				return nil, nil, errors.New("Unsafe archive segment")
			}
		}
		lower := strings.ToLower(f.Name)
		if folded[lower] {
			return nil, nil, errors.New("Duplicate or case-colliding archive member")
		}
		folded[lower] = true
		if !f.Mode().IsRegular() || f.FileInfo().IsDir() {
			return nil, nil, errors.New("Only regular archive files are accepted")
		}
		if f.Flags&1 != 0 || (f.Method != zip.Store && f.Method != zip.Deflate) {
			return nil, nil, errors.New("Encrypted or unsupported ZIP member")
		}
		if f.UncompressedSize64 > maxArchive {
			return nil, nil, errors.New("ZIP member exceeds limit")
		}
		total += f.UncompressedSize64
		if total > maxTotal {
			return nil, nil, errors.New("Expanded archive exceeds limit")
		}
		compressed := f.CompressedSize64
		if compressed < 1 {
			compressed = 1
		}
		if f.UncompressedSize64 > compressed*200 {
			return nil, nil, errors.New("Archive expansion ratio exceeds limit")
		}
		if f.Name != "pack.json" && !(len(parts) >= 3 && parts[0] == "shards" && shardRE.MatchString(parts[1])) {
			return nil, nil, errors.New("Unexpected transport member")
		}
		stream, e := f.Open()
		if e != nil {
			return nil, nil, e
		}
		b, e := io.ReadAll(io.LimitReader(stream, maxArchive+1))
		stream.Close()
		if e != nil {
			return nil, nil, e
		}
		if uint64(len(b)) != f.UncompressedSize64 {
			return nil, nil, errors.New("ZIP member size mismatch")
		}
		files[f.Name] = b
	}
	for name := range files {
		parts := strings.Split(name, "/")
		for i := 1; i < len(parts); i++ {
			if folded[strings.ToLower(strings.Join(parts[:i], "/"))] {
				return nil, nil, errors.New("File/directory collision")
			}
		}
	}
	m, e := asObject(files["pack.json"])
	if e != nil {
		return nil, nil, e
	}
	if len(m) != 3 || m["format"] != format {
		return nil, nil, errors.New("Unsupported experimental transport manifest")
	}
	ids, e := stringArray(m["shards"])
	if e != nil || len(ids) < 1 || len(ids) > 32 {
		return nil, nil, errors.New("Invalid transport shard inventory")
	}
	seen := map[string]bool{}
	last := ""
	for _, id := range ids {
		if !shardRE.MatchString(id) || seen[id] || id < last {
			return nil, nil, errors.New("Invalid shard inventory")
		}
		seen[id] = true
		last = id
		if _, ok := files["shards/"+id+"/manifest.json"]; !ok {
			return nil, nil, errors.New("Missing shard manifest")
		}
	}
	head, ok := m["head"].(string)
	if !ok || !seen[head] {
		return nil, nil, errors.New("Invalid head identity")
	}
	for name := range files {
		if name != "pack.json" && !seen[strings.Split(name, "/")[1]] {
			return nil, nil, errors.New("Inventory differs from members")
		}
	}
	return files, m, nil
}
func jsonRows(raw []byte) ([]obj, error) {
	out := []obj{}
	for _, line := range bytes.Split(raw, []byte("\n")) {
		if len(line) == 0 {
			continue
		}
		m, e := asObject(line)
		if e != nil {
			return nil, e
		}
		out = append(out, m)
	}
	return out, nil
}
func numeric(v any) int {
	switch n := v.(type) {
	case json.Number:
		i, _ := n.Int64()
		return int(i)
	case float64:
		return int(n)
	}
	return 0
}
func str(v any) string { s, _ := v.(string); return s }
func Inspect(data, key []byte) (obj, error) {
	start := time.Now()
	if len(key) != 1344 {
		return nil, errors.New("Supply a separate 1344-byte trusted hybrid public key")
	}
	files, transport, e := unpack(data)
	if e != nil {
		return nil, e
	}
	archiveFS, e := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if e != nil {
		return nil, e
	}
	ids, _ := stringArray(transport["shards"])
	head := str(transport["head"])
	checks := []obj{}
	manifests := map[string]obj{}
	allPass := true
	for _, id := range ids {
		mark := time.Now()
		shardFS, err := fs.Sub(archiveFS, "shards/"+id)
		if err != nil {
			return nil, err
		}
		r := axm.VerifyFS(shardFS, id, key)
		checks = append(checks, obj{"implementation": "Go / Genesis verification", "shard_id": id, "status": r.Status, "errors": r.Errors, "profiles_unchecked": r.ProfilesUnchecked, "milliseconds": float64(time.Since(mark).Microseconds()) / 1000})
		if r.Status != "PASS" {
			allPass = false
			continue
		}
		raw := files["shards/"+id+"/manifest.json"]
		if identity(raw) != id {
			return nil, errors.New("Directory identity differs from canonical manifest identity")
		}
		m, e := asObject(raw)
		if e != nil {
			return nil, e
		}
		manifests[id] = m
	}
	result := obj{"format": format, "status": "FAIL", "archive_sha256": digest(data), "archive_bytes": len(data), "trusted_key_sha256": digest(key), "head": head, "checks": checks, "records": []obj{}, "scope": "Artifact integrity and evidence binding. Claim truth, source completeness, currentness and publisher identity require separate evidence."}
	if !allPass {
		result["milliseconds"] = float64(time.Since(start).Microseconds()) / 1000
		return result, nil
	}
	previous := map[string][]string{}
	referenced := map[string]bool{}
	for id, m := range manifests {
		p := []string{}
		if v, ok := m["supersedes"]; ok {
			p, e = stringArray(v)
			if e != nil {
				return nil, e
			}
		}
		for _, ancestor := range p {
			if _, ok := manifests[ancestor]; !ok || ancestor == id {
				return nil, errors.New("History is incomplete or self-referential")
			}
			referenced[ancestor] = true
		}
		previous[id] = p
	}
	roots := []string{}
	for id := range manifests {
		if !referenced[id] {
			roots = append(roots, id)
		}
	}
	if len(roots) != 1 || roots[0] != head {
		return nil, errors.New("Transport must have one unambiguous history head")
	}
	ordered := []string{}
	active := map[string]bool{}
	done := map[string]bool{}
	var visit func(string) error
	visit = func(id string) error {
		if active[id] {
			return errors.New("History contains a cycle")
		}
		if done[id] {
			return nil
		}
		active[id] = true
		for _, p := range previous[id] {
			if e := visit(p); e != nil {
				return e
			}
		}
		delete(active, id)
		done[id] = true
		ordered = append(ordered, id)
		return nil
	}
	if e = visit(head); e != nil {
		return nil, e
	}
	if len(ordered) != len(ids) {
		return nil, errors.New("Unconnected records in transport")
	}
	records := []obj{}
	for _, id := range ordered {
		prefix := "shards/" + id + "/"
		m := manifests[id]
		metadata := m["metadata"].(obj)
		er, e := jsonRows(files[prefix+"graph/entities.jsonl"])
		if e != nil {
			return nil, e
		}
		entities := map[string]string{}
		for _, r := range er {
			entities[str(r["entity_id"])] = str(r["label"])
		}
		pr, e := jsonRows(files[prefix+"graph/provenance.jsonl"])
		if e != nil {
			return nil, e
		}
		cr, e := jsonRows(files[prefix+"graph/claims.jsonl"])
		if e != nil {
			return nil, e
		}
		sources := []obj{}
		sourceByHash := map[string]string{}
		for _, v := range m["sources"].([]any) {
			s := v.(obj)
			name := str(s["path"])
			sourceByHash[str(s["hash"])] = name
			sources = append(sources, obj{"path": name, "hash": s["hash"], "bytes": len(files[prefix+name]), "text": strings.ToValidUTF8(string(files[prefix+name]), "�")})
		}
		claims := []obj{}
		for _, c := range cr {
			ev := []obj{}
			for _, p := range pr {
				if p["claim_id"] != c["claim_id"] {
					continue
				}
				name := sourceByHash[str(p["source_hash"])]
				b := files[prefix+name]
				a, z := numeric(p["byte_start"]), numeric(p["byte_end"])
				if a < 0 || z < a || z > len(b) {
					return nil, errors.New("Evidence outside source")
				}
				copy := obj{}
				for k, v := range p {
					copy[k] = v
				}
				copy["source"] = name
				copy["text"] = string(b[a:z])
				ev = append(ev, copy)
			}
			c["subject_label"] = entities[str(c["subject"])]
			label := str(c["object"])
			if x, ok := entities[label]; ok {
				label = x
			}
			c["object_label"] = label
			c["evidence"] = ev
			claims = append(claims, c)
		}
		records = append(records, obj{"id": id, "title": metadata["title"], "created_at": metadata["created_at"], "claims": claims, "sources": sources, "supersedes": previous[id], "suite": m["suite"], "manifest": m})
	}
	result["status"] = "PASS"
	result["records"] = records
	result["milliseconds"] = float64(time.Since(start).Microseconds()) / 1000
	return result, nil
}
func repack(files map[string][]byte) ([]byte, error) {
	var output bytes.Buffer
	w := zip.NewWriter(&output)
	names := []string{}
	for name := range files {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		h := &zip.FileHeader{Name: name, Method: zip.Deflate}
		h.SetMode(0644)
		h.Modified = time.Date(1980, 1, 1, 0, 0, 0, 0, time.UTC)
		f, e := w.CreateHeader(h)
		if e != nil {
			return nil, e
		}
		if _, e = f.Write(files[name]); e != nil {
			return nil, e
		}
	}
	if e := w.Close(); e != nil {
		return nil, e
	}
	return output.Bytes(), nil
}
func Challenge(data, key []byte, mode string) (obj, error) {
	files, m, e := unpack(data)
	if e != nil {
		return nil, e
	}
	original := digest(data)
	changed := ""
	candidate := data
	anchor := append([]byte{}, key...)
	if mode == "wrong-key" {
		if len(anchor) != 1344 {
			return nil, errors.New("Wrong key size")
		}
		anchor[0] ^= 1
		changed = "Detached trust anchor, byte 0"
	} else {
		name := "shards/" + str(m["head"]) + "/"
		position := 0
		switch mode {
		case "source":
			name += "content/source.txt"
		case "ed25519":
			name += "sig/manifest.sig"
		case "mldsa":
			name += "sig/manifest.sig"
			position = 64
		default:
			return nil, errors.New("Unknown challenge")
		}
		b := append([]byte{}, files[name]...)
		if len(b) <= position {
			return nil, errors.New("Missing challenge target")
		}
		b[position] ^= 1
		files[name] = b
		candidate, e = repack(files)
		if e != nil {
			return nil, e
		}
		changed = fmt.Sprintf("%s, byte %d", name, position)
	}
	r, e := Inspect(candidate, anchor)
	if e != nil {
		return nil, e
	}
	r["challenge"] = obj{"mode": mode, "changed": changed, "original_sha256": original, "original_preserved": digest(data) == original}
	return r, nil
}
func decode(value any, cap int) ([]byte, error) {
	s, ok := value.(string)
	if !ok || len(s) > ((cap+2)/3)*4 {
		return nil, errors.New("Input exceeds byte limit")
	}
	b, e := base64.StdEncoding.Strict().DecodeString(s)
	if e != nil {
		return nil, e
	}
	if len(b) > cap {
		return nil, errors.New("Input exceeds byte limit")
	}
	return b, nil
}

// Request is the common API boundary used by the native CLI and browser worker.
func Request(operation, archive, key, mode string) (map[string]any, error) {
	data, e := decode(archive, maxArchive)
	if e != nil {
		return nil, e
	}
	anchor, e := decode(key, 1344)
	if e != nil {
		return nil, e
	}
	switch operation {
	case "open":
		return Inspect(data, anchor)
	case "challenge":
		return Challenge(data, anchor, mode)
	default:
		return nil, errors.New("Unsupported operation")
	}
}

// VerifyVector opens a bounded test-vector ZIP in memory and runs the same kernel.
func VerifyVector(data, key []byte) (map[string]any, error) {
	if len(data) > maxArchive || len(key) != 1344 {
		return nil, errors.New("Vector input limit")
	}
	z, e := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if e != nil {
		return nil, e
	}
	if len(z.File) > 1024 {
		return nil, errors.New("Vector member limit")
	}
	var total uint64
	for _, f := range z.File {
		total += f.UncompressedSize64
		if total > maxTotal || f.UncompressedSize64 > maxArchive {
			return nil, errors.New("Expanded vector limit")
		}
		if !fs.ValidPath(f.Name) || f.Mode()&fs.ModeSymlink != 0 {
			return nil, errors.New("Unsafe vector path")
		}
	}
	r := axm.VerifyFS(z, "conformance-vector", key)
	b, _ := json.Marshal(r)
	var out map[string]any
	json.Unmarshal(b, &out)
	return out, nil
}
