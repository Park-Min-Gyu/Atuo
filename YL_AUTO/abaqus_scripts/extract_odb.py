# -*- coding: utf-8 -*-
"""
extract_odb.py  (Abaqus Python 전용: Abaqus 2019=Py2.7, 2024+=Py3 모두 동작하도록 작성)

사용:  abaqus python extract_odb.py <job.odb> <out.csv> <rp_node_label>

LOAD_RP 의 U1~U3, RF1~RF3 를 스텝/증분별로 CSV 로 저장한다.
  1순위: History Output (Node xxx.<rp_label>)
  2순위: Field Output (LOAD_RP 노드셋)  - history 가 없을 때 대비
CSV 열: step, step_time, total_time, U1, U2, U3, RF1, RF2, RF3
"""
import sys

from odbAccess import openOdb

KEYS = ["U1", "U2", "U3", "RF1", "RF2", "RF3"]


def find_history_region(step, rp_label):
    suffix = "." + str(rp_label)
    for name in step.historyRegions.keys():
        if name.startswith("Node ") and name.endswith(suffix):
            return step.historyRegions[name]
    return None


def find_rp_nodeset(odb):
    ra = odb.rootAssembly
    for key in ra.nodeSets.keys():
        if key.upper() == "LOAD_RP":
            return ra.nodeSets[key]
    for inst in ra.instances.values():
        for key in inst.nodeSets.keys():
            if key.upper() == "LOAD_RP":
                return inst.nodeSets[key]
    return None


def rows_from_history(odb, rp_label):
    rows = []
    for sname in odb.steps.keys():
        st = odb.steps[sname]
        reg = find_history_region(st, rp_label)
        if reg is None:
            return None
        hos = reg.historyOutputs
        for k in KEYS:
            if k not in hos.keys():
                return None
        data = [hos[k].data for k in KEYS]
        for i in range(len(data[0])):
            t = data[0][i][0]
            rows.append([sname, t, st.totalTime + t] + [d[i][1] for d in data])
    return rows


def rows_from_field(odb):
    nset = find_rp_nodeset(odb)
    if nset is None:
        return None
    rows = []
    for sname in odb.steps.keys():
        st = odb.steps[sname]
        for fr in st.frames:
            fo = fr.fieldOutputs
            if "U" not in fo.keys() or "RF" not in fo.keys():
                continue
            u = fo["U"].getSubset(region=nset).values[0].data
            rf = fo["RF"].getSubset(region=nset).values[0].data
            t = fr.frameValue
            rows.append([sname, t, st.totalTime + t, u[0], u[1], u[2], rf[0], rf[1], rf[2]])
    return rows


def main():
    odb_path, out_csv, rp_label = sys.argv[1], sys.argv[2], int(sys.argv[3])
    odb = openOdb(path=odb_path, readOnly=True)
    src = "history"
    rows = rows_from_history(odb, rp_label)
    if not rows:
        src = "field"
        rows = rows_from_field(odb)
    odb.close()
    if not rows:
        sys.stderr.write("ERROR: LOAD_RP 결과를 ODB 에서 찾지 못했습니다.\n")
        sys.exit(2)
    f = open(out_csv, "w")
    f.write("# source=%s\n" % src)
    f.write("step,step_time,total_time," + ",".join(KEYS) + "\n")
    for r in rows:
        f.write("%s,%.8g,%.8g,%s\n" % (r[0], r[1], r[2], ",".join("%.10g" % v for v in r[3:])))
    f.close()
    print("OK %d rows (%s) -> %s" % (len(rows), src, out_csv))


if __name__ == "__main__":
    main()
