// Ambient declarations so TypeScript and editors resolve side-effect style
// imports such as `import "./compiled.css"` in app/layout.tsx. The Palette CLI
// bundler is what actually emits/scopes the CSS; these imports carry no JS
// bindings, so an empty module declaration is all TypeScript needs.
declare module "*.css"
declare module "*.scss"
