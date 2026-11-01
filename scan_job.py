import json, scanner
d = scanner.scan()
json.dump(d, open("data.json", "w"), separators=(",", ":"))
print(d["session"], d["lastBar"], [p["sym"] for p in d["picks"]])
