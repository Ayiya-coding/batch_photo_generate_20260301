import { useLocation, Link } from "wouter";
import { cn } from "@/lib/utils";
import {
  Upload,
  SlidersHorizontal,
  PenLine,
  Sparkles,
  CheckCircle,
  LayoutGrid,
  CircleUser,
  ImageUp,
  Database,
  Settings,
} from "lucide-react";

interface NavItem {
  id: string;
  label: string;
  icon: React.ReactNode;
  path: string;
}

const navItems: NavItem[] = [
  { id: "upload", label: "素材上传", icon: <Upload className="w-[18px] h-[18px]" />, path: "/" },
  { id: "preprocess", label: "预处理", icon: <SlidersHorizontal className="w-[18px] h-[18px]" />, path: "/preprocess" },
  { id: "prompt", label: "提示词", icon: <PenLine className="w-[18px] h-[18px]" />, path: "/prompt" },
  { id: "generate", label: "批量生图", icon: <Sparkles className="w-[18px] h-[18px]" />, path: "/generate" },
  { id: "review", label: "审核分类", icon: <CheckCircle className="w-[18px] h-[18px]" />, path: "/review" },
  { id: "template", label: "模板管理", icon: <LayoutGrid className="w-[18px] h-[18px]" />, path: "/template" },
  { id: "wideface", label: "宽脸图", icon: <CircleUser className="w-[18px] h-[18px]" />, path: "/wideface" },
  { id: "process", label: "画面处理", icon: <ImageUp className="w-[18px] h-[18px]" />, path: "/process" },
  { id: "backup", label: "备份恢复", icon: <Database className="w-[18px] h-[18px]" />, path: "/backup" },
  { id: "settings", label: "系统设置", icon: <Settings className="w-[18px] h-[18px]" />, path: "/settings" },
];

export default function Sidebar() {
  const [location] = useLocation();

  const isActive = (path: string) => {
    if (path === "/") return location === "/";
    return location.startsWith(path);
  };

  return (
    <aside className="w-[200px] h-screen bg-white flex flex-col border-r border-border shrink-0">
      {/* Logo区域 */}
      <div className="h-[60px] bg-primary flex items-center gap-2 px-4">
        <span className="text-white font-bold text-lg">AI</span>
        <span className="text-white font-semibold text-base">AI图片生成</span>
      </div>

      {/* 导航列表 */}
      <nav className="flex-1 py-4 px-3 overflow-y-auto custom-scrollbar">
        <div className="flex flex-col gap-1">
          {navItems.map((item) => (
            <Link key={item.id} href={item.path}>
              <div
                className={cn(
                  "flex items-center gap-3 px-4 h-11 rounded-lg text-sm transition-all duration-150 cursor-pointer",
                  isActive(item.path)
                    ? "bg-accent text-accent-foreground font-semibold"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground font-normal"
                )}
              >
                <span
                  className={cn(
                    "shrink-0",
                    isActive(item.path) ? "text-primary" : "text-muted-foreground"
                  )}
                >
                  {item.icon}
                </span>
                <span className="truncate">{item.label}</span>
              </div>
            </Link>
          ))}
        </div>
      </nav>
    </aside>
  );
}
