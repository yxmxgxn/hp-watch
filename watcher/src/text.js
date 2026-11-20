// 言葉の照合用に、文字をそろえる（題名と探す言葉の両方に同じ処理をする）
//   ・全角半角・大文字小文字（NFKC・小文字）
//   ・名前によくある異体字（NFKC では同じにならない）: 山﨑／山崎、櫻井／桜井、有澤／有沢 など
const VARIANTS = {
  "﨑": "崎", "嵜": "崎", "髙": "高", "櫻": "桜", "澤": "沢", "嶋": "島", "邉": "辺", "邊": "辺",
  "齋": "斎", "齊": "斉", "德": "徳", "國": "国", "濱": "浜", "廣": "広", "眞": "真",
};
const RE = new RegExp(`[${Object.keys(VARIANTS).join("")}]`, "g");

export const norm = (s) => String(s || "").normalize("NFKC").toLowerCase().replace(RE, (c) => VARIANTS[c]);
export const compact = (s) => norm(s).replace(/\s+/g, "");  // 空白も除く（「川名 凜」と「川名凜」）
