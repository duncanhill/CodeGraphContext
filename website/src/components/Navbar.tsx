
import React from "react";

function handleScroll(e: React.MouseEvent<HTMLAnchorElement>) {
  const href = e.currentTarget.getAttribute('href');
  if (href && href.startsWith('#')) {
    e.preventDefault();
    const id = href.replace('#', '');
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }
}


const Navbar: React.FC = () => {
  return (
    <nav className="fixed top-6 left-1/2 transform -translate-x-1/2 z-50 w-[90vw] max-w-6xl">
      <div
        className="rounded-full backdrop-blur-2xl shadow-2xl border border-white/30 px-8 py-3 flex items-center justify-center"
        style={{
          background: 'linear-gradient(to bottom, hsl(var(--card) / 0.6), hsl(var(--graph-node-1) / 0.35))',
          borderColor: 'rgba(255,255,255,0.18)',
          boxShadow: '0 8px 32px 0 rgba(31, 38, 135, 0.37)',
        }}
      >
        <ul className="flex space-x-8 font-semibold text-base text-[hsl(var(--foreground))]">
          <li><a href="#features" className="px-4 py-2 rounded-full hover:bg-[hsl(var(--primary)/0.15)] hover:text-[hsl(var(--primary))] transition" onClick={handleScroll}>Features</a></li>
          <li><a href="#bundle-registry" className="px-4 py-2 rounded-full hover:bg-[hsl(var(--primary)/0.15)] hover:text-[hsl(var(--primary))] transition" onClick={handleScroll}>Pre-indexed Repositories</a></li>
          <li><a href="#bundle-generator" className="px-4 py-2 rounded-full hover:bg-[hsl(var(--primary)/0.15)] hover:text-[hsl(var(--primary))] transition" onClick={handleScroll}>Custom Bundle</a></li>
          <li><a href="#cookbook" className="px-4 py-2 rounded-full hover:bg-[hsl(var(--primary)/0.15)] hover:text-[hsl(var(--primary))] transition" onClick={handleScroll}>Cookbook</a></li>
          <li><a href="#demo" className="px-4 py-2 rounded-full hover:bg-[hsl(var(--primary)/0.15)] hover:text-[hsl(var(--primary))] transition" onClick={handleScroll}>Demo</a></li>
          <li><a href="#installation" className="px-4 py-2 rounded-full hover:bg-[hsl(var(--primary)/0.15)] hover:text-[hsl(var(--primary))] transition" onClick={handleScroll}>Installation</a></li>
          <li><a href="#testimonials" className="px-4 py-2 rounded-full hover:bg-[hsl(var(--primary)/0.15)] hover:text-[hsl(var(--primary))] transition" onClick={handleScroll}>Testimonials</a></li>
        </ul>
      </div>
    </nav>
  );
};

export default Navbar;
