(*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *)

(* A set of class intervals *)
type t

val of_list : ClassInterval.t list -> t

val to_list : t -> ClassInterval.t list

val show : t -> string

val show_list : ClassInterval.t list -> string

val equal : t -> t -> bool

val meet : t -> t -> t

val join : t -> t -> t

val empty : t

val bottom : t

val top : t

val is_top : t -> bool

val is_empty : t -> bool

val lower_bound_exn : t -> int

val upper_bound_exn : t -> int

val less_or_equal : left:t -> right:t -> bool

val pp : Format.formatter -> t -> unit

val compute_intervals : ClassHierarchyGraph.t -> t ClassHierarchyGraph.ClassNameMap.t

module SharedMemory : sig
  val add : class_name:ClassHierarchyGraph.class_name -> interval:t -> unit

  val get : class_name:ClassHierarchyGraph.class_name -> t option

  val store : t ClassHierarchyGraph.ClassNameMap.t -> unit

  val of_type : Type.t option -> t

  val of_definition : Ast.Statement.Define.t Ast.Node.t -> t
end
