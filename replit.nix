{ pkgs }: {
  deps = [
    pkgs.python311
    pkgs.python311Packages.pip
  ];
  env = {
    LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
      pkgs.stdenv.cc.cc.lib
    ];
  };
}
