"""Isolated standard-fixture checks for a recognized generated HTMLParser API."""
import ast
from html.parser import HTMLParser
import json
import sys


def main():
    files = json.loads(sys.stdin.read(750001))
    checked, defects, unrecognized = 0, [], []
    fixture = '<a href="/1"><strong>Title &amp; data</strong></a><span> by author</span><p>body</p><a href="/?page=2">Next</a><footer>end</footer>'
    for item in files:
        tree = ast.parse(item['source'])
        aliases = {alias.asname or alias.name for node in tree.body if isinstance(node,ast.ImportFrom)
                   and node.module == 'html.parser' for alias in node.names if alias.name == 'HTMLParser'}
        for node in tree.body:
            if not isinstance(node,ast.ClassDef) or node.decorator_list or not any(isinstance(base,ast.Name) and base.id in aliases for base in node.bases):
                continue
            scope = {alias:HTMLParser for alias in aliases}
            scope.update(__name__='generated_helper_probe',__builtins__={
                '__build_class__':__build_class__, 'super':super, 'object':object, 'dict':dict, 'list':list,
                'str':str, 'int':int, 'bool':bool, 'len':len, 'tuple':tuple, 'set':set, 'range':range,
                'enumerate':enumerate, 'isinstance':isinstance, 'min':min, 'max':max,
                'staticmethod':staticmethod,'property':property,'ValueError':ValueError})
            try:
                exec(compile(ast.Module(body=[node],type_ignores=[]), '<generated-helper>', 'exec'),scope)
                parser = scope[node.name]()
                parser.feed(fixture)
                links = getattr(parser,'links',None)
                if not isinstance(links,list) or any(not isinstance(link,(list,tuple)) or len(link)!=2 for link in links):
                    unrecognized.append(item['path'] + ':' + node.name)
                    continue
                # A .links attribute alone does not define pair orientation.
                # Probe only the recognized (href, text) interface; other
                # valid representations remain for the semantic model audit.
                if len(links) != 2 or [link[0] for link in links] != ['/1','/?page=2'] or not all(isinstance(link[1],str) for link in links):
                    unrecognized.append(item['path'] + ':' + node.name + ':unknown_link_interface')
                    continue
                checked += 1
                expected = [('/1','Title & data'),('/?page=2','Next')]
                observed = [(href,text.strip()) for href,text in links]
                if observed != expected:
                    defects.append({'path':item['path'],'problem':node.name + ' does not stop link text at </a> or handle nested tags/entities correctly',
                                    'counterexample':json.dumps({'html':fixture,'expected':expected,'observed':observed})})
            except Exception as error:
                unrecognized.append(item['path'] + ':' + node.name + ':' + type(error).__name__)
    print(json.dumps({'checked':checked,'defects':defects,'unrecognized':unrecognized}))


if __name__ == '__main__':
    main()
