export function icon(name){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  const use=document.createElementNS('http://www.w3.org/2000/svg','use');
  svg.classList.add('icon');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
  use.setAttribute('href','/static/workbench-icons.svg#'+name);svg.append(use);return svg;
}
