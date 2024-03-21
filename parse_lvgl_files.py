from    pycparser               import CParser, c_ast, c_generator
from    pathlib                 import Path
from    tqdm                    import tqdm
import  subprocess
import  json
import  os
import  re

class Parser :

    Include_Path    = []
    Cpp             = "gcc"
    Ignore_Files    = set \
        ( ( "lv_style_gen.h", "lv_objx_templ.h" , "lv_color_op.h"
          #, "lv_lru.h", "lv_ll.h", "lv_cache_entry_private.h"
          )
        )
    Cache           = \
        { "Types"        : {}
        , "Enums"        : {}
        , "Variables"    : {}
        , "Functions"    : {}
        , "Unions"       : {}
        , "Widget_Base"  : {}
        }

    def __init__ (self, lvgl_api, * files) :
        self.files       = []
        self.Gen         = c_generator.CGenerator ()
        self.files.append (lvgl_api)
        self.files.extend (files)
        if self.files :
            pbar = tqdm (self.files, miniters = 1)
            for f in pbar :
                if not f.name in self.Ignore_Files :
                    pbar.set_description ("Processing %-50s" % f.name)
                    ast = self.parse_file (f)
                    for node in ast :
                        hname = node.__class__.__name__
                        hname = f"_handle_{hname}"
                        hfct  = getattr (self, hname, None)
                        if hfct :
                            hfct (node)
                        else :
                            print (f"No handler for {hname}")
            self._find_widgets ()
    # end def __init__

    def save_cache (self, file_name) :
        fn = Path (file_name)
        with fn.open ("w") as f :
            C = {}
            for k, v in sorted (self.Cache.items ()) :
                C1 = {}
                for k1, v1 in sorted (v.items ()) :
                    C1 [k] = v
                C [k] = v
            json.dump (C, f, indent = 2)
            print ("Cache saved to file %s" % fn)
            for k, v in self.Cache.items () :
                print (" - %-20s: %3d" % (k, len (v)))
    # end def save_cache

    @classmethod
    def Load_Cache (cls, file_name) :
        with Path (file_name).open () as f :
            cls.Cache = json.load (f)
        return cls (())
    # end def load_cache

    def _store_item (self, kind, key, node, ** data) :
        data ["file"] = str (Path (node.coord.file).resolve ())
        self.Cache [kind] [key] = data
        return data
    # end def _store_item

    def _handle_Decl (self, node) :
        if isinstance (node.type, c_ast.FuncDecl) :
            self._handle_FuncDef (node)
        elif isinstance (node.type, c_ast.TypeDecl) :
            self._handle_Variable (node)
        elif isinstance (node.type, c_ast.Enum) :
            self._handle_Enum (node)
        elif isinstance (node.type, c_ast.Union) :
            self._handle_Union (node)
    # end def _handle_Decl

    def _handle_Typedef (self, node) :
        if isinstance (node, c_ast.Typedef) :
            Key = node.name
        else :
            Key = node.type.names [0]
        if Key not in self.Cache ["Types"] :
            type                = getattr (node.type, "type", None)
            SF                  = {}
            Is_Function         = {}
            #real_type           = Key
            if type and isinstance (type, c_ast.Struct) :
                for d in type.decls or () :
                    fname    = d.name
                    if (   isinstance (d.type,      c_ast.PtrDecl)
                       and isinstance (d.type.type, c_ast.FuncDecl)
                       ) :
                        ft       = d.type.type
                        args     = self._get_function_args (ft)
                        ftype    = { "return_type" : self.Gen.visit (ft.type)
                                   , "args"        : args
                                   }
                    elif (   isinstance (d.type.type, c_ast.Union)
                         ) :
                        self._handle_Union (d.type)
                        ftype = {"Is_Union" : d.type.type.name}
                    else :
                        ftype    = self.Gen.visit (d.type)
                    SF [fname] = ftype
            if type and isinstance (type, c_ast.Enum) :
                self._handle_Enum (node.type)
            if type and isinstance (type, c_ast.Union) :
                self._handle_Union (node.type)
            if type and isinstance (type, c_ast.FuncDecl) :
                return_type    = self.Gen.visit (type.type)
                if return_type == "void" :
                    return_type = None
                args            = self._get_function_args (type)
                Is_Function ["return_type"] = return_type
                Is_Function ["args"]        = args
            add = {}
            if isinstance (node, c_ast.Typedef) :
                add ["type"] = self.Gen.visit (node.type.type)
            return self._store_item \
                ( "Types", Key, node
                , const         = "const"  in node.quals
                , name          = Key
                , Is_Function   = Is_Function
                , Struct_Fields = SF
                , Is_Enum       = type and isinstance (type, c_ast.Enum)
                , Is_Union      = type and isinstance (type, c_ast.Union)
                , ** add
                )
        return self.Cache ["Types"] [Key]
    # end def _handle_Typedef

    def _get_function_args (self, ftype) :
        args = {}
        for i, arg in enumerate (ftype.args or ()) :
            if isinstance (arg, c_ast.EllipsisParam) :
                args ["*"] = None
            else :
                at = self.Gen.visit (arg.type)
                an = arg.name
                if not an and at != "void" :
                    an = f"arg_{i}"
                args [an] = at
        return args
    # end _get_function_args

    def _handle_FuncDef (self, node) :
        if isinstance (node, c_ast.FuncDef) :
            Key = node.decl.name
        else :
            Key = node.name
        if Key not in self.Cache ["Functions"] :
            if isinstance (node, c_ast.FuncDef) :
                name       = node.decl.name
                type       = node.decl.type
            else :
                name       = node.name
                type       = node.type
            return_type    = self.Gen.visit (type.type)
            if return_type == "void" :
                return_type = None
            args = self._get_function_args (type)
            return self._store_item \
                ( "Functions", Key, node
                , name          = name
                , return_type   = return_type
                , args          = args
                )
        else :
            return self.Cache ["Functions"] [Key]
    # end def _handle_FuncDef

    def _handle_Variable (self, node) :
        Key = node.name
        if Key not in self.Cache ["Variables"] :
            type_name = self._handle_Typedef (node.type) ["name"]
            self._store_item \
                ( "Variables", Key, node
                , name          = Key
                , const         = "const"  in node.quals
                , extern        = "extern" in node.storage
                , type_name     = type_name
                )
        return self.Cache ["Variables"] [Key]
    # end def _handle_Variable

    def _handle_Enum (self, node) :
        Key = node.type.name
        if Key is None and isinstance (node, c_ast.TypeDecl) :
            Key = node.declname
        if Key is None :
            T = node.type
            Key = os.path.commonprefix \
                ([e.name for e in T.values if not e.name.startswith ("_")])
            if not Key :
                Key = os.path.commonprefix ([e.name for e in T.values])
        Key = Key.strip ("_")
        if not Key :
            breakpoint ()
        if Key not in self.Cache ["Enums"] :
            V = {}
            value       = 0
            for e in node.type.values :
                en = e.name
                if e.value :
                    value = self.Gen.visit (e.value)
                    try:
                        value = eval (value)
                    except :
                        pass
                V [en] = value
                try:
                    value += 1
                except :
                    pass
            return self._store_item ("Enums", Key, node, values = V)
        return self.Cache ["Enums"] [Key]
    # end def _handle_Enum

    def _handle_Union (self, node) :
        Key = node.type.name
        if Key is None and isinstance (node, c_ast.TypeDecl) :
            Key = node.declname
        if Key is None :
            Key = os.path.commonprefix \
                ([e.name for e in node.type.values])
        Key = Key.strip ("_")
        if Key not in self.Cache ["Unions"] :
            UF = []
            for d in node.type.decls or () :
                UF.append ((self.Gen.visit (d.type), d.name))
            return self._store_item ("Unions", Key, node, fields = UF)
        return self.Cache ["Unions"] [Key]
    # end def _handle_Union

    def _find_widgets (self) :
        cls_name_pat = re.compile ("lv_(.+)_class")
        self.Cache ["Widgets"] = {}
        vars = tqdm (self.Cache ["Variables"].values (), miniters = 1)
        for v in vars :
            if v ["type_name"] == "lv_obj_class_t" :
                name = v ["name"]
                if m := cls_name_pat.search (name) :
                    vars.set_description (f"Found class {name:50}")
                    c_file          = Path (v ["file"]).with_suffix (".c")
                    if name not in self.Cache ["Widget_Base"] :
                        cls_var     = self._find_variable (c_file, v ["name"])
                        base_class  = self._get_initializer \
                            (cls_var, "base_class").replace ("&", "").strip ()
                        if base_class == "0" :
                            base_class = None
                        else :
                            base_class = base_class [3:-6]
                    else :
                        base_class = self.Cache ["Widget_Base"] [name]
                    cls_name    = m.group (1)
                    directory   = str (c_file.parent)
                    cls_def  = { "base_class"       : base_class
                               , "directory"        : directory
                               }
                    self.Cache ["Widgets"] [cls_name] = cls_def
                    self.Cache ["Widget_Base"] [name] = base_class
                else :
                    print ("No class found", name)
    # end def _find_widgets

    def _find_variable (self, file_name, var_name) :
        ast       = self.parse_file (str (file_name))
        file_name = file_name.name
        for node in ast :
            if file_name in node.coord.file :
                if (   isinstance (node,      c_ast.Decl)
                   and isinstance (node.type, c_ast.TypeDecl)
                   and (node.type.declname == var_name)
                   ) :
                    return node
    # end def _find_variable

    def _get_initializer (self, var, field, as_node = False) :
        for i in var.init :
            if i.name [0].name == field :
                result = i.expr
                if not as_node :
                    result = self.Gen.visit (i.expr)
                return result
    # end def _get_initializer

    def _glob_files (self, directory) :
        result = []
        for f in directory.glob ("**/*.h") :
            result.append (f)
        return result
    # end def _glob_files

    @classmethod
    def preprocess_file (cls, file_name, cpp_path = "cpp", cpp_args = "") :
        """ Preprocess a file using cpp.

            file_name:
                Name of the file you want to preprocess.

            cpp_path:
            cpp_args:
                Refer to the documentation of parse_file for the meaning of these
                arguments.

            When successful, returns the preprocessed file's contents.
            Errors from cpp will be printed out.
        """
        path_list = [cpp_path]
        if isinstance (cpp_args, list) :
            path_list.extend (cpp_args)
        elif cpp_args != "" :
            path_list.append (cpp_args)
        path_list.append (file_name)

        try:
            # Note the use of universal_newlines to treat all newlines
            # as \n for Python's purpose
            text = subprocess.check_output \
                (path_list, universal_newlines = True, encoding = "utf-8")
        except OSError as err :
            raise RuntimeError \
                ( "Unable to invoke 'cpp'.  "
                  "Make sure its path was passed correctly\n"
                  "Original error: %s" % e
                )
        return text
    # end def preprocess_file

    @classmethod
    def parse_file (cls, file_name) :
        inc_files = ["-I%s" % f for f in cls.Include_Path]
        cpp_args  = [ "-nostdinc"
                          , "-E"
                          ]
        cpp_args.extend (inc_files)
        cpp_args.extend ( ( "-D__attribute__(x)"
                          , "-DPYCPARSER"
                          , "-DLV_CONF_INCLUDE_SIMPLE"
                          , "-Wno-microsoft-include"
                          )
                        )
        text = cls.preprocess_file (file_name, cls.Cpp, cpp_args)
        return CParser ().parse (text, file_name)
    # end def parse_file

# end class Parser

def Break_On_Exception () :
    import sys
    def info(type, value, tb):
        if hasattr(sys, 'ps1') or not sys.stderr.isatty():
            # You are in interactive mode or don't have a tty-like
            # device, so call the default hook
            sys.__excepthook__ (type, value, tb)
        else:
            import traceback, pdb
            # You are not in interactive mode; print the exception
            traceback.print_exception(type, value, tb)
            print ()
            # ... then star the debugger in post-mortem mode
            pdb.pm ()

    sys.excepthook = info
# end def Break_On_Exception

if __name__ == "__main__" :
    import argparse
    fakeclib = str (Path ("fake_libc_include"))
    parser   = argparse.ArgumentParser ()
    parser.add_argument ("cache", type = str, default = "ncache.json")
    parser.add_argument ("--gcc", type = str, default = "gcc")
    parser.add_argument ("--fake-include", type = str, default = fakeclib)
    parser.add_argument ("-c", "--config", type = str, default = "lv_configs")
    parser.add_argument ("-b", "--lvgl-base", type = str, default = "../lvgl")
    parser.add_argument ("-s", "--start-header-file", type = str, default = "lvgl.h")
    parser.add_argument ("--load-parents", type = str )
    cmd    = parser.parse_args ()
    Parser.Include_Path.append (cmd.fake_include)
    if cmd.gcc :
        Parser.Cpp  = cmd.gcc
    Break_On_Exception ()
    root            = Path (__file__).parent
    lv_base         = Path (cmd.lvgl_base)
    lv_s_base       = lv_base / "src"
    Parser.Include_Path.append (Path (cmd.config))
    Parser.Include_Path.append (lv_base / "..")
    Parser.Include_Path.append (lv_base)
    if cmd.load_parents :
        with open (cmd.load_parents) as f :
            Cache = json.load (f)
            Parser.Cache ["Widget_Base"] = Cache.get ("Widget_Base", {})
    p  = Parser ( Path (cmd.start_header_file)
#                , lv_s_base / "libs/fsdrv/lv_fsdrv.h"
                )
    if cmd.cache :
        p.save_cache (cmd.cache)
