import { lazy, Suspense } from "react";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Route, Switch } from "wouter";
import ErrorBoundary from "./components/ErrorBoundary";
import PageLoader from "./components/PageLoader";
import { ThemeProvider } from "./contexts/ThemeContext";
import { UploadProvider } from "./contexts/UploadContext";

// 首页静态导入（保证首屏速度）
import MaterialUpload from "./pages/MaterialUpload";

// 其余页面懒加载
const Preprocess = lazy(() => import("./pages/Preprocess"));
const PromptConfig = lazy(() => import("./pages/PromptConfig"));
const BatchGenerate = lazy(() => import("./pages/BatchGenerate"));
const ReviewClassify = lazy(() => import("./pages/ReviewClassify"));
const TemplateManage = lazy(() => import("./pages/TemplateManage"));
const WideFace = lazy(() => import("./pages/WideFace"));
const ImageProcess = lazy(() => import("./pages/ImageProcess"));
const BackupRestore = lazy(() => import("./pages/BackupRestore"));
const Settings = lazy(() => import("./pages/Settings"));
const NotFound = lazy(() => import("./pages/NotFound"));

function Router() {
  return (
    <Suspense fallback={<PageLoader />}>
      <Switch>
        <Route path="/" component={MaterialUpload} />
        <Route path="/preprocess" component={Preprocess} />
        <Route path="/prompt" component={PromptConfig} />
        <Route path="/generate" component={BatchGenerate} />
        <Route path="/review" component={ReviewClassify} />
        <Route path="/template" component={TemplateManage} />
        <Route path="/wideface" component={WideFace} />
        <Route path="/process" component={ImageProcess} />
        <Route path="/backup" component={BackupRestore} />
        <Route path="/settings" component={Settings} />
        <Route path="/404" component={NotFound} />
        {/* Final fallback route */}
        <Route component={NotFound} />
      </Switch>
    </Suspense>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider defaultTheme="light">
        <UploadProvider>
          <TooltipProvider>
            <Toaster />
            <Router />
          </TooltipProvider>
        </UploadProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
