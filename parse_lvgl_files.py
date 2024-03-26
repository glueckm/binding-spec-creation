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
        , "Structs"      : {}
        , "Enums"        : {}
        , "Variables"    : {}
        , "Functions"    : {}
        , "Unions"       : {}
        , "Class_Base"   : {}
        , "Classes"      : {}
        }
    Struct_Forward_Decl  = {}

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
        Types   = self.Cache ["Types"]
        Structs = self.Cache ["Structs"]
        for tn, sn in self.Struct_Forward_Decl.items () :
            if tn in Types and sn in Structs :
                Types [tn] ["Struct_Fields"] = Structs [sn] ["Struct_Fields"]
        self._identify_classes             ()                
        self._assign_functions_to_classes  ()
        self._check_enums                  ()
    # end def __init__

    def _identify_classes (self) :
        Classes = self.Cache ["Classes"]
        for t in self.Cache ["Types"].values () :
            if t ["Is_Struct"] and "fake_libc_include" not in t ["file"] :
                name        = t ["name"]
                widget_name = name [3:-2]
                if widget_name not in Classes :
                    Classes [name [3:]] = { "base_class" : None
                                          , "c_type"     : name
                                          , "is_widget"  : False
                                          , "file"       : t ["file"]
                                          }
    # end def _identify_classes
    
    def _is_method (self, fspec, class_name, base_class, widget = None) :
        result      = False
        fargs       = fspec ["args"]
        if class_name.endswith ("_t") :
            class_name = class_name [:-2]
        if len (fargs) >= 1 :
            at          = tuple (fargs.values ()) [0]
            if at.endswith ("*") :
                at = at [:-1].strip ()
            pos_types   = set ()
            for ct in class_name, base_class, widget :
                if not ct : continue
                ct = f"lv_{ct}_t"
                pos_types.add (ct)
                if ct.startswith ("const") :
                    pos_types.add (ct [6:])
                    pos_types.add (f"struct _{ct [6:]}")
                else :
                    pos_types.add (f"const {ct}")
                    pos_types.add (f"struct _{ct}")
                    pos_types.add (f"const struct _{ct}")
            result = at in pos_types
        fspec ["is_method"] = result
        return result
    # end def _is_method

    def _assign_functions_to_classes (self) :
        Classes     = self.Cache ["Classes"]
        Functions   = self.Cache ["Functions"]
        Types       = self.Cache ["Types"]
        ## sorting by negative len assures that the longest type name is matched 
        ## first
        pat         = re.compile \
            ("^lv_(%s)_"
            % ( "|".join (sorted ( Classes.keys ()
                                 , key = lambda k : -len (k)
                                 )
                         )
              ,
              )
            )
        Stripped_Class_Names = set \
            ( cn if not cn.endswith ("_t") else cn [:-2]
              for cn in Classes.keys () 
            ) 
        self.Stripped_Class_Name_Pat = re.compile \
            ("^lv_(%s)_"
            % ( "|".join (sorted ( Stripped_Class_Names
                                 , key = lambda k : -len (k)
                                 )
                         )
              ,
              )
            )
        funcs = tqdm (Functions.items ())
        for f, fspec in funcs :
            funcs.set_description (f"Check {f:30}")
            cm         = self.Stripped_Class_Name_Pat.search (f)
            if cm :
                class_name          = cm.group (1)
                fspec ["part_of"]   = class_name
                cspec               = Classes.get (class_name)
                if not cspec :
                    cspec           = Classes.get (f"{class_name}_t")
                if "properties" not in cspec :
                    cspec ["properties"] = P = {}
                    SF = Types.get \
                        (cspec ["c_type"], {}).get ("Struct_Fields", {})
                    for n, t in SF.items () :
                        P [n] = { "c_type" : t}
                is_method           = self._is_method \
                    ( fspec
                    , class_name
                    , cspec ["base_class"]
                    , "obj" if cspec.get ("is_widget") else None
                    )
                key = "methods" if is_method else "class_functions"
                cspec.setdefault (key, []).append (f)
                is_add_event        = False
                for at in fspec ["args"].values () :
                    if at == "lv_event_cb_t" :
                        for s in ("_add_event_cb", "_event_add") :
                            if f.endswith (s) :
                                is_add_event = f [:-len (s)]
                                break
                if is_add_event :
                    head = is_add_event
                    get_event_count = f"{head}_get_event_count"
                    get_event_dsc   = f"{head}_get_event_dsc"
                    if (   get_event_count in Functions
                       and get_event_dsc   in Functions
                       ) :
                        is_add_event = (get_event_count, get_event_dsc)
                    else :
                        is_add_event = f != "lv_event_add"
                fspec ["is_add_event"] = is_add_event
                if is_add_event != False :
                    cspec ["has_events"]   = True
                if is_method :
                    args    = fspec ["args"]
                    fget    = f"lv_{class_name}_get_"
                    fset    = f"lv_{class_name}_set_"
                    props   = cspec ["properties"]
                    n       = f [len (fget):]
                    if   (len (args) == 1) and  f.startswith (fget) :
                        if n not in props :
                            props [n] = {"computed" : True}
                        c_type = fspec ["return_type"]
                        if "c_type" not in props [n] :
                            props [n] ["c_type"] = c_type
                        #else :
                        #    if props [n] ["c_type"] != c_type :
                        #        raise ValueError ("Different c_types")
                        props [n] ["get"] = f
                    elif (len (args) == 2) and  f.startswith (fset) :
                        if n not in props :
                            props [n] = {"computed" : True}
                        c_type = tuple (args.values ()) [1]
                        if "c_type" not in props [n] :
                            props [n] ["c_type"] = c_type
                        props [n] ["set"] = f
                if f.endswith ("_create") :
                    cspec ["constructor"] = f
                    fspec ["constructor"] = class_name
    # end def _assign_functions_to_classes

    def _check_enums (self) :
        value_replace   = {}
        Enums           = self.Cache ["Enums"]
        Types           = self.Cache ["Types"]
        Classes         = self.Cache ["Classes"]
        for tn, tspec in Types.items () :
            espec = Enums.get (tn)
            if espec :
                tspec ["Is_Enum"] = espec
        for en, espec in Enums.items () :
            v = tuple (espec ["values"].values ()) [0]
            if isinstance (v, str) :
                p = re.compile (r"(\d+)L\s*<<")
                if v.endswith ("U") :
                    v = v [:-1]
                try :
                    v = p.sub (r"\1 <<", v)
                    v = eval (v)
                except :
                    pass
            espec ["int-values"] = not isinstance (v, str)
            cm = self.Stripped_Class_Name_Pat.search (en.lower ())
            if cm :
                class_name          = cm.group (1)
            else :
                class_name          = None
            if class_name :
                cspec               = Classes.get (class_name)
                if not cspec :
                    class_name      =f"{class_name}_t"
                    cspec           = Classes [class_name]
                cspec.setdefault ("enums", []).append (en)
            espec ["part_of"]       = class_name
            values    = {}
            for n, v in espec ["values"].items () :
                if not n.startswith ("_") :
                    values [n] = v
            if not values : ### this is the symbol enum
                for n, v in espec ["values"].items () :
                    if n.startswith ("_") :
                        values [n] = v
            en = os.path.commonprefix (tuple (values.keys ())).lstrip ("_") [3:]
            if not en :
                en = en.strip ("_") [3:-2].upper ()
            else :
                #fn = next (iter (values.keys ()))
                if not en.endswith ("_") :
                    en = en.rsplit ("_") [0]
                else :
                    en = en [:-1]
            py_name      = en
            short_values = {}
            ori2short    = {}
            if class_name and class_name.endswith ("_t") :
                class_name = class_name [:-2]
            if class_name and en.startswith (class_name.upper ()) :
                py_name = en [len (class_name) + 1:]
                if len (py_name) < 3 :
                    if py_name :
                        en = en [:-1-len (py_name)]
                    py_name = ""
                if not py_name :
                    strip_len    = len (class_name) + 4
                    for n,v in values.items () :
                        short_values [n [strip_len:]] = v
                    if len (values) > 1 :
                        split_key = f"{py_name}_"
                        for n,v in values.items () :
                            short           = n.split (split_key) [1]
                            short_values [short] = v
                            ori2short    [n]     = short
                    else :
                        short_values = values
            if py_name :
                if len (values) > 1 : 
                    split_key = f"{py_name}_"
                    for n,v in values.items () :
                        short                = n.split (split_key) [1]
                        short_values [short] = v
                        ori2short    [n]     = short
                else :
                    short_values = values
            espec ["common_after_class_name"] = py_name
            espec ["orig_values"]             = espec ["values"]
            if ori2short :
                p = re.compile ("(%s)" % ("|".join (ori2short.keys ()), ))
                for n, ov in short_values.items () :
                    short_values [n] = p.sub \
                        (lambda m : ori2short [m.group (1)], str (ov))
            values = {}
            for n, v in short_values.items () :
                if str (n) [0].isdigit () :
                    n = f"_{n}"
                if str (v).startswith ("1L << ") :
                    v = v.replace ("1L << ", "1 << ")
                if str (v).startswith ("0x") and str (v).endswith ("U") :
                    v = v [:-1]
                values [n] = v
            espec ["values"]                  = values
    # end def _check_enums

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
                print (" - %-20s: %4d" % (k, len (v)))
    # end def save_cache

    @classmethod
    def Load_Cache (cls, file_name) :
        with Path (file_name).open () as f :
            cls.Cache = json.load (f)
        return cls (())
    # end def load_cache

    def _store_item (self, kind, key, node, ** data) :
        data ["file"] = str (Path (node.coord.file).resolve ())
        if "fake_libc_include" not in data ["file"] :
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
        elif isinstance (node.type, c_ast.Struct) :
            if node.type.decls :
                SF = self._get_struct_fields (node.type)
                self.Cache ["Structs"] [node.type.name] = \
                    { "name"          : node.type.name
                    , "Struct_Fields" : SF
                    }
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
                SF              = self._get_struct_fields (type)
                if not SF :
                    if "fake_libc_include" not in node.coord.file :
                        self.Struct_Forward_Decl [Key] = type.name
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
                , Is_Struct     = type and isinstance (type, c_ast.Struct)
                , Is_Enum       = type and isinstance (type, c_ast.Enum)
                , Is_Union      = type and isinstance (type, c_ast.Union)
                , ** add
                )
        return self.Cache ["Types"] [Key]
    # end def _handle_Typedef

    def _get_struct_fields (self, type) :
        SF = {}
        for d in type.decls or () :
            fname    = d.name
            if (    isinstance (d.type,      c_ast.PtrDecl)
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
                ftype  = {"Is_Union" : d.type.type.name}
            else :
                ftype  = self.Gen.visit (d.type)
            SF [fname] = ftype
        return SF
    # end def _get_struct_fields
    
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
        self.Cache ["Classes"] = {}
        vars = tqdm (self.Cache ["Variables"].values (), miniters = 1)
        for v in vars :
            if v ["type_name"] == "lv_obj_class_t" :
                name = v ["name"]
                if m := cls_name_pat.search (name) :
                    vars.set_description (f"Found class {name:50}")
                    c_file          = Path (v ["file"]).with_suffix (".c")
                    if name not in self.Cache ["Class_Base"] :
                        cls_var     = self._find_variable (c_file, v ["name"])
                        base_class  = self._get_initializer \
                            (cls_var, "base_class").replace ("&", "").strip ()
                        if base_class == "0" :
                            base_class = None
                        else :
                            base_class = base_class [3:-6]
                    else :
                        base_class = self.Cache ["Class_Base"] [name]
                    cls_name    = m.group (1)
                    directory   = str (c_file.parent)
                    cls_def  = { "base_class"       : base_class
                               , "c_type"           : f"lv_{cls_name}_t"
                               , "is_widget"        : True
                               , "directory"        : directory
                               }
                    self.Cache ["Classes"] [cls_name] = cls_def
                    self.Cache ["Class_Base"] [name]  = base_class
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
                          #, "-DDOXYGEN"
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
    parser.add_argument \
        ( "cache",                      type    = str
        , help    = "Filename for the resulting json file"
        )
    parser.add_argument \
        ( "--gcc",                      type = str, default = "gcc"
        , help = "Executable which should be used for preprocessing the files "
                 "before sent to the pycparser."
        )
    parser.add_argument \
        ( "--fake-include",             type = str, default = fakeclib
        , help = "A path to a directory containing fake include files for the "
                 "system include files normally provided by teh compiler.\n"
                 "Fake includes files a required because pycparser cannot "
                 "parse the original system includes"
        )
    parser.add_argument \
        ( "-c", "--config",             type = str, default = "lv_configs"
        , help = "Path to a directory containing a lv_conf.h file"
        )
    parser.add_argument \
        ( "-b", "--lvgl-base",          type = str, default = "../lvgl"
        , help = "Root directory for the lvgl source code"
        )
    parser.add_argument \
        ( "-s", "--start-header-file",  type = str, default = "../lvgl/lvgl.h"
        , help = "Full path to the header file which will be used as the start "
                 "file for parsing."
        )
    parser.add_argument \
        ( "--load-parents", type = str
        , help = "Preload the widget parent information from this file to "
                 "speedup the parsing.\n"
                 "If this file name is not provided the script will not only "
                 "parse the header files but also the C files to extract the "
                 "parent classes for widget definitions."
        )
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
            Parser.Cache ["Class_Base"] = Cache.get ("Class_Base", {})
    p  = Parser ( Path (cmd.start_header_file))
    if cmd.cache :
        p.save_cache (cmd.cache)
