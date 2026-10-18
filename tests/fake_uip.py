#!/usr/bin/env python3
"""Minimal stand-in for `uip tm …` used by the tests. State lives in $FAKE_UIP_STATE (JSON).

Only the commands `xr sync` and `xr demo` call are implemented; anything else fails loudly.
Every call is appended to state["calls"] so tests can assert on the CLI traffic.
"""
import json
import os
import sys
import uuid

MULTI = {"--step", "--labels", "--object-ids", "--object-types", "--data-types", "--test-case-keys"}


def parse(argv):
    words, opts, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--") or a in ("-y",):
            vals = []
            i += 1
            while i < len(argv) and not argv[i].startswith("--"):
                vals.append(argv[i])
                i += 1
            if a in MULTI:
                opts.setdefault(a, []).extend(vals)
            else:
                opts[a] = vals[0] if vals else True
        else:
            words.append(a)
            i += 1
    return words, opts


def out(data, ok=True, msg=""):
    if ok:
        print(json.dumps({"Result": "Success", "Code": "Fake", "Data": data}))
        sys.exit(0)
    else:
        print(json.dumps({"Result": "Failure", "Message": msg, "Retry": "RetryWillNotFix"}))
        sys.exit(1)


def main():
    path = os.environ["FAKE_UIP_STATE"]
    st = json.load(open(path)) if os.path.exists(path) else {}
    st.setdefault("projects", ["XRSYNC"])
    st.setdefault("cases", {})  # id -> case
    st.setdefault("steps", {})  # case id -> [step]
    st.setdefault("fields", [])  # "ObjectType:Name"
    st.setdefault("sets", {})  # key -> {Id, TestSetKey, Name, Description, members: [case ids]}
    st.setdefault("executions", {})  # id -> {logs: [log]}
    st.setdefault("steplogs", {})  # log id -> [step log]
    st.setdefault("values", {})  # case id -> {name: row}
    st.setdefault("labels", {})  # case id -> [names]
    st.setdefault("calls", [])
    st.setdefault("fail_on", None)
    words, o = parse(sys.argv[1:])
    cmd = " ".join(words)
    st["calls"].append(cmd)

    def save():
        json.dump(st, open(path, "w"), indent=1)

    save()
    if st["fail_on"] and st["fail_on"] in cmd:
        save()
        out(None, ok=False, msg=f"injected failure on {cmd}")

    if cmd == "login status":
        out({"Status": "Logged in", "Identity": "fake@example.com", "Tenant": "Fake"})
    elif cmd == "login refresh":
        out({"BaseUrl": "https://tm.example", "OrganizationName": "org", "TenantName": "ten", "AccessToken": "tok"})
    elif cmd == "tm project list":
        out([{"ProjectKey": p, "Id": f"pid-{p}"} for p in st["projects"] if p.startswith(o.get("--filter", ""))])
    elif cmd == "tm testcases delete":
        cid = next(c["Id"] for c in st["cases"].values() if c["TestCaseKey"] == o["--test-case-key"])
        del st["cases"][cid]
        for ts in st["sets"].values():
            ts["members"] = [m for m in ts["members"] if m != cid]
        save()
        out({"Result": "Deleted"})
    elif cmd == "tm testsets delete":
        del st["sets"][o["--test-set-key"]]
        save()
        out({"Result": "Deleted"})  # like real TM: its TM test executions stay
    elif cmd == "tm executions list":
        out([{"Id": eid, "Name": e["Name"], "TestSetId": e["TestSetId"], "Status": "Finished"}
             for eid, e in st["executions"].items() if e["TestSetId"] == o.get("--test-set-id", e["TestSetId"])])
    elif cmd == "tm testcases list":
        f = o.get("--filter", "")
        out([c for c in st["cases"].values() if c["Name"].startswith(f) or c["TestCaseKey"].startswith(f)])
    elif cmd == "tm testcases create":
        cid = str(uuid.uuid4())
        n = len(st["cases"]) + 1
        st["cases"][cid] = {"Id": cid, "TestCaseKey": f"{o['--project-key']}:{n}", "Name": o["--name"],
                            "Description": o.get("--description", ""), "PreCondition": ""}
        # Like real TM: an empty value row is seeded for every custom field definition.
        st["values"][cid] = {f.split(":", 1)[1]: {"Id": str(uuid.uuid4()), "Name": f.split(":", 1)[1], "Value": ""}
                             for f in st["fields"] if f.startswith("TestCase:")}
        save()
        out(st["cases"][cid])
    elif cmd == "tm testcases update":
        case = next(c for c in st["cases"].values() if c["TestCaseKey"] == o["--test-case-key"])
        for flag, field in (("--name", "Name"), ("--description", "Description"), ("--pre-condition", "PreCondition")):
            if flag in o:
                case[field] = "" if o[flag] is True else o[flag]
        save()
        out(case)
    elif cmd == "tm testcases steps list":
        out(st["steps"].get(o["--test-case-id"], []))
    elif cmd == "tm testcases steps add":
        rows = st["steps"].setdefault(o["--test-case-id"], [])
        for raw in o["--step"]:
            s = json.loads(raw)
            rows.append({"Id": str(uuid.uuid4()), "OrderNo": len(rows), "Description": s.get("description"),
                         "ExpectedResult": s.get("expectedResult"), "ClipboardData": s.get("clipboardData")})
        save()
        out({"AddedCount": len(o["--step"])})
    elif cmd == "tm testcases steps delete":
        for rows in st["steps"].values():
            rows[:] = [r for r in rows if r["Id"] != o["--step-id"]]
        save()
        out({"Result": "Deleted"})
    elif cmd == "tm customfield list":
        types = o.get("--object-types") or ["TestCase", "TestSet", "Requirement"]
        out([{"Name": f.split(":", 1)[1]} for f in st["fields"] if f.split(":", 1)[0] in types])
    elif cmd == "tm customfield create":
        st["fields"].append(f"{o['--object-type']}:{o['--name']}")
        save()
        out({"Name": o["--name"]})
    elif cmd == "tm customfield value list":
        out(list(st["values"].get(o.get("--object-id"), {}).values()))
    elif cmd == "tm customfield value get":
        row = st["values"].get(o["--object-id"], {}).get(o["--name"])
        out(row) if row else out(None, ok=False, msg="value not found")
    elif cmd in ("tm customfield value create", "tm customfield value update"):
        if f"{o['--object-type']}:{o['--name']}" not in st["fields"]:
            out(None, ok=False, msg="no such custom field definition")
        rows = st["values"].setdefault(o["--object-id"], {})
        if st.get("clobber_writes"):  # like real TM right after creation: the empty row provisioning wins
            st["clobber_writes"] -= 1
            save()
            out(rows.get(o["--name"]))
        if cmd.endswith("create") and o["--name"] in rows:
            out(rows[o["--name"]])  # like real TM: Success, but nothing is written
        if cmd.endswith("update") and o["--name"] not in rows:
            out(None, ok=False, msg="value not found")
        rows[o["--name"]] = {"Id": str(uuid.uuid4()), "Name": o["--name"], "Value": o["--value"]}
        save()
        out(rows[o["--name"]])
    elif cmd == "tm objectlabel list":
        names = []
        for oid in o["--object-ids"]:
            names += [n for n in st["labels"].get(oid, []) if n.startswith(o.get("--filter", ""))]
        out([{"Name": n} for n in sorted(set(names))])
    elif cmd == "tm objectlabel add":
        for oid in o["--object-ids"]:
            st["labels"][oid] = sorted(set(st["labels"].get(oid, [])) | set(o["--labels"]))
        save()
        out({"Result": "Added"})
    elif cmd == "tm objectlabel remove":
        for oid in o["--object-ids"]:
            st["labels"][oid] = [n for n in st["labels"].get(oid, []) if n not in o["--labels"]]
        save()
        out({"Result": "Removed"})
    elif cmd == "tm testsets list":
        f = o.get("--filter", "")
        out([{k: v for k, v in ts.items() if k != "members"} for ts in st["sets"].values()
             if ts["TestSetKey"].startswith(f) or ts["Name"].startswith(f)])
    elif cmd == "tm testsets create":
        key = f"{o['--project-key']}:{100 + len(st['sets'])}"
        st["sets"][key] = {"Id": str(uuid.uuid4()), "TestSetKey": key, "Name": o["--name"],
                           "Description": o.get("--description", ""), "members": []}
        save()
        out({"TestSetKey": key, "Name": o["--name"], "Description": o.get("--description", "")})
    elif cmd == "tm testsets update":
        st["sets"][o["--test-set-key"]]["Name"] = o["--name"]
        save()
        out({"Result": "Updated"})
    elif cmd == "tm testsets list-testcases":
        # Like real TM: rows carry no Id.
        out([{k: v for k, v in st["cases"][cid].items() if k != "Id"} for cid in st["sets"][o["--test-set-key"]]["members"]])
    elif cmd in ("tm testcases add", "tm testcases remove"):
        keys = [k for v in o["--test-case-keys"] for k in v.split(",")]
        ids = [c["Id"] for c in st["cases"].values() if c["TestCaseKey"] in keys]
        members = st["sets"][o["--test-set-key"]]["members"]
        if cmd.endswith("add"):
            members += [i for i in ids if i not in members]
        else:
            members[:] = [i for i in members if i not in ids]
        save()
        out({"Result": "Added" if cmd.endswith("add") else "Removed"})
    elif cmd == "tm testsets run":
        eid = str(uuid.uuid4())
        ts = st["sets"][o["--test-set-key"]]
        st["executions"][eid] = {"TestSetId": ts["Id"], "Name": f"{ts['Name']} - run", "logs": [
            {"Id": str(uuid.uuid4()), "TestCaseId": cid, "TestCase": {"ObjKey": st["cases"][cid]["TestCaseKey"]},
             "Result": "None", "TestExecutionId": eid}
            for cid in st["sets"][o["--test-set-key"]]["members"]]}
        save()
        out({"ExecutionId": eid, "Status": "Pending", "StartTime": "2026-10-17T00:00:00.000Z"})
    elif cmd == "tm executions testcaselogs list":
        out(st["executions"][o["--execution-id"]]["logs"])
    elif cmd == "tm testcaselog start":
        lg = next(l for l in st["executions"][o["--execution-id"]]["logs"] if l["TestCaseId"] == o["--test-case-id"])
        st["steplogs"][lg["Id"]] = [
            {"Id": str(uuid.uuid4()), "TestStepId": s["Id"], "Result": "None", "Comment": "", "HasError": False,
             "TestCaseLogId": lg["Id"], "OrderNo": s["OrderNo"], "Description": s["Description"]}
            for s in st["steps"].get(o["--test-case-id"], [])]
        save()
        out({"Id": lg["Id"], "Result": "None"})
    elif cmd == "tm teststeplog list":
        out(st["steplogs"].get(o["--test-case-log-id"], []))
    elif cmd == "tm testcaselog finish":
        lg = next(l for l in st["executions"][o["--execution-id"]]["logs"] if l["TestCaseId"] == o["--test-case-id"])
        lg.update(Result=o["--result"], ExecutionStart="2026-10-17T00:00:01.000Z", ExecutionEnd="2026-10-17T00:00:09.000Z")
        for sl in json.loads(o.get("--step-logs", "[]")):
            row = next(r for r in st["steplogs"][lg["Id"]] if r["Id"] == sl["id"])
            row.update(Result=sl["result"], Comment=sl.get("comment", ""))
        save()
        out(lg)
    else:
        save()
        out(None, ok=False, msg=f"fake uip: unsupported command {cmd!r}")


if __name__ == "__main__":
    main()
