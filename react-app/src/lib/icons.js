import { BookOpen, Calculator, Clock, Code2, ImagePlus, Search, Wrench } from 'lucide-react';

const TOOL_ICONS = {
  web_search: Search,
  fetch_url: BookOpen,
  calculator: Calculator,
  get_datetime: Clock,
  generate_image: ImagePlus,
  run_python: Code2,
};

export const toolIcon = (name) => TOOL_ICONS[name] || Wrench;
